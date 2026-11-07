"""Local operator interface. Mutations always need a fresh yes/no prompt."""
import argparse
import os
from pathlib import Path
from .runner import CommandRunner
from .state import StateStore
from .nginx import list_sites,render_proxy_site,SiteChange,plan_site_change
from .transactions import apply_site_change,rollback_site_change
from .ports import list_listeners,format_listeners
from .systemd import list_units,plan_systemd_action,apply_systemd_action,create_managed_unit
from .compose import discover_compose,plan_compose_action,apply_compose_action,plan_restart_override,apply_restart_override
from .cleanup import inspect_cleanup,apply_cleanup
from .certbot import plan_certificate,run_certificate,list_certificates,renewal_dry_run
from .discovery import find_backend_owners

AVAILABLE=Path('/etc/nginx/sites-available')
ENABLED=Path('/etc/nginx/sites-enabled')

def confirm(label):
    return input(f'{label}\nВведите ДА для подтверждения: ').strip()=='ДА'

def select(items,title,describe=str):
    if not items:print('Нет доступных записей');return None
    print('\n'+title)
    for i,v in enumerate(items,1):print(f'{i:>3}. {describe(v)}')
    answer=input('Номер (0 — назад): ').strip()
    if not answer.isdecimal():return None
    n=int(answer)
    return items[n-1] if 1<=n<=len(items) else None

def _display_site(site):
    return f'{site.name} [{"включён" if site.enabled else "отключён"}] {", ".join(site.domains) or "?"} → {", ".join(site.endpoints) or "?"}'+ (' [неоднозначно]' if site.ambiguous else '')

def _ensure_root():
    if os.geteuid()!=0:print('Операция требует sudo/root');return False
    return True

def _site_action(action,runner,state,dry_run):
    sites=list_sites(AVAILABLE,ENABLED)
    selected=select([s for s in sites if s.enabled==(action=='disable')], 'Сайты Nginx',_display_site)
    if selected is None:return
    if selected.path.is_symlink():print('Неуправляемая ссылка: операция отменена');return
    if not _ensure_root() and not dry_run:return
    change=plan_site_change(action,selected,AVAILABLE,ENABLED)
    print('План:',action,selected.name,'домены:',', '.join(selected.domains))
    if action=='disable':
        for s in sites:
            if s.name!=selected.name and set(s.endpoints)&set(selected.endpoints):print('ВНИМАНИЕ: общий backend также у',s.name)
    if not dry_run and not confirm('Изменить состояние сайта?'):return
    result=apply_site_change(change,runner,state,dry_run=dry_run)
    print(result.message)
    if not result.ok or action!='disable' or dry_run:return
    if selected.ambiguous or len(selected.endpoints)!=1:
        print('Backend нельзя определить уверенно. Остановка службы запрещена.');return
    endpoint=selected.endpoints[0].removeprefix('http://')
    owners=find_backend_owners(endpoint,runner)
    if len(owners)!=1:
        print('Владелец backend не определён однозначно. Служба не остановлена.');return
    owner=owners[0]
    print('Обнаружен backend:',owner.name)
    if owner.kind!='compose':
        print('Остановка backend этого типа недоступна автоматически');return
    try:
        matches=[(p,sv) for p in discover_compose(runner) for sv in p.services if p.name+'/'+sv==owner.name and owner.container_id in p.container_ids]
    except (RuntimeError,ValueError):matches=[]
    if len(matches)!=1:
        print('Неоднозначная связь Compose: остановка запрещена');return
    project,service=matches[0]
    mode=input('Backend: 0 — оставить, 1 — stop, 2 — stop + restart=no: ').strip()
    if mode not in ('1','2'):return
    if not confirm('Остановить '+owner.name+'?'):return
    result=apply_compose_action(plan_compose_action(project,(service,),'stop'),runner,state)
    print(result.message)
    if not result.ok or mode!='2':return
    ids=tuple(sorted(o.container_id for o in owners if o.kind=='compose' and o.name==owner.name))
    if not ids:print('Контейнер не найден');return
    try:restart=plan_restart_override(ids,runner)
    except RuntimeError as exc:print(exc);return
    print('Текущие политики:',restart)
    if confirm('Отключить автозапуск docker update --restart=no?'):
        print(apply_restart_override(restart,runner,state))

def _create_site(runner,state,dry_run):
    if not _ensure_root() and not dry_run:return
    backend_type=input('Backend: 1 — existing systemd, 2 — new systemd, 3 — Compose, 4 — IP/port [4]: ').strip() or '4'
    if backend_type not in ('1','2','3','4'):
        print('Неверный тип backend');return
    if backend_type=='1':
        try:unit=select(list_units(runner),'Службы',lambda x:x[0]+' ['+x[1]+']')
        except RuntimeError as exc:print(exc);return
        if not unit:return
        print('Выбрана служба',unit[0],'; укажите её доступный IP и порт')
    elif backend_type=='2':
        print('Для создания systemd-службы будет запущен отдельный мастер')
        _systemd_menu_create(runner,state,dry_run)
        print('Укажите IP и порт приложения для Nginx')
    elif backend_type=='3':
        try:projects=discover_compose(runner)
        except (RuntimeError,ValueError) as exc:print(exc);return
        project=select(projects,'Проект Compose',lambda p:p.name)
        if not project:return
        service=select(project.services,'Сервис Compose')
        if not service:return
        print('Выбран',project.name+'/'+service,'; используйте опубликованный на хосте порт')
    name=input('Имя конфигурации (ASCII, без пути): ').strip()
    domain=input('Домены через пробел: ').strip().split()
    address=input('IP backend [127.0.0.1]: ').strip() or '127.0.0.1'
    try:
        port=int(input('Порт backend: ').strip())
        content=render_proxy_site(tuple(domain),address,port)
        change=SiteChange('create',name,AVAILABLE,ENABLED,content)
        existing=list_sites(AVAILABLE,ENABLED)
        if set(domain)&{d for s in existing for d in s.domains}:
            print('Конфликт существующего домена');return
    except ValueError as exc:print('Ошибка:',exc);return
    print('Новая конфигурация:\n'+content)
    if not dry_run and not confirm('Создать и включить виртуальный хост?'):return
    print(apply_site_change(change,runner,state,dry_run=dry_run).message)

def _systemd_menu_create(runner,state,dry_run):
    name=input('Имя службы: ').strip();user=input('Пользователь: ').strip()
    workdir=Path(input('Рабочая директория: ').strip())
    import shlex
    try:argv=tuple(shlex.split(input('Команда запуска: ')))
    except ValueError as exc:print(exc);return
    if not _ensure_root() and not dry_run:return
    if not dry_run and not confirm('Создать managed systemd unit?'):return
    print(create_managed_unit(name,user,workdir,argv,runner,dry_run=dry_run).message)

def _systemd_menu(runner,state,dry_run):
    choice=input('systemd: 1 — список/управление, 2 — создать службу: ').strip()
    if choice=='1':
        try:units=list_units(runner)
        except RuntimeError as exc:print(exc);return
        selected=select(units,'Службы',lambda x:f'{x[0]} [{x[1]}]')
        if not selected:return
        verb=input('Действие: start / stop / enable / disable: ').strip()
        try:action=plan_systemd_action(selected[0],verb)
        except ValueError as exc:print(exc);return
        if not _ensure_root() and not dry_run:return
        if not dry_run and not confirm(f'{verb} {selected[0]}?'):return
        print(apply_systemd_action(action,runner,state,dry_run=dry_run).message)
    if choice=='2':_systemd_menu_create(runner,state,dry_run)

def _compose_menu(runner,state,dry_run):
    try:projects=discover_compose(runner)
    except (RuntimeError,ValueError) as exc:print('Docker:',exc);return
    project=select(projects,'Docker Compose',lambda p:f'{p.name} [{", ".join(p.services)}]')
    if project is None:return
    print('Compose:',project.config_file,'\nСервисы:',', '.join(project.services))
    operation=input('1 — stop, 2 — start, 3 — отключить restart, 4 — остановить и очистить: ').strip()
    if operation in ('1','2'):
        service=select(project.services,'Выберите сервис')
        if not service:return
        action=plan_compose_action(project,(service,),'stop' if operation=='1' else 'start')
        if not _ensure_root() and not dry_run:return
        if not dry_run and not confirm(f'{action.verb} {project.name}/{service}?'):return
        print(apply_compose_action(action,runner,state,dry_run=dry_run).message)
    elif operation=='3':
        if not _ensure_root() and not dry_run:return
        try:policy=plan_restart_override(project.container_ids,runner)
        except RuntimeError as exc:print(exc);return
        print('План отключения restart:',policy)
        print('ВАЖНО: Compose может восстановить restart при пересоздании контейнера')
        if not dry_run and not confirm('Применить docker update --restart=no?'):return
        print(apply_restart_override(policy,runner,state,dry_run=dry_run))
    elif operation=='4':
        if not _ensure_root() and not dry_run:return
        try:plan=inspect_cleanup(project,runner)
        except (RuntimeError,ValueError) as exc:print('Ошибка инвентаризации:',exc);return
        print('Контейнеры:',plan.container_ids,'\nСохраняемые mounts:',plan.protected_mounts)
        print('Образы для удаления:',plan.image_candidates,'\nБлокировки:',plan.blockers)
        if plan.blockers:print('Очистка заблокирована');return
        if not dry_run and not confirm('Данные вне mounts будут потеряны! Подтверждаете их сохранность?'):return
        if not dry_run and not confirm('Удалить контейнеры ТОЛЬКО выбранного проекта?'):return
        result=apply_cleanup(plan,runner,state,confirmed=True,accept_writable_layer=True,dry_run=dry_run)
        print(result.message)

def _certbot_menu(runner,state,dry_run):
    choice=input('Certbot: 1 — сертификаты, 2 — получить, 3 — renewal dry-run: ').strip()
    if choice=='1':print(list_certificates(runner).stdout);return
    if choice=='3':
        print('DRY RUN: certbot renew --dry-run' if dry_run else renewal_dry_run(runner).stdout);return
    if choice!='2':return
    site=select([s for s in list_sites(AVAILABLE,ENABLED) if s.enabled and not s.ambiguous], 'Сайт',_display_site)
    if not site:return
    try:plan=plan_certificate(site,runner)
    except ValueError as exc:print(exc);return
    if not _ensure_root() and not dry_run:return
    if not dry_run and not confirm('Запустить certbot для '+', '.join(plan.domains)+'?'):return
    print(run_certificate(plan,runner,state,dry_run=dry_run).message)

def _restore_backup(runner,state,dry_run):
    backup_root=state.root/'backups'
    backups=sorted(backup_root.glob('*.json')) if backup_root.is_dir() else []
    selected=select(backups,'Резервные копии Nginx',lambda p:p.name)
    if selected is None:return
    if not _ensure_root() and not dry_run:return
    print('Будет восстановлена резервная копия:',selected.name)
    if not dry_run and not confirm('Подтвердите восстановление конфигурации Nginx'):return
    print(rollback_site_change(selected.name,runner,state,AVAILABLE,ENABLED,dry_run=dry_run).message)

def menu(runner,state,dry_run):
    while True:
        print('\n=== SERVER MANAGER ===\n1. Сайты\n2. Добавить сайт\n3. Отключить сайт\n4. Включить сайт\n5. systemd\n6. Docker Compose\n7. Certbot\n8. Порты\n9. nginx -t\n10. Журнал\n0. Выход')
        try:
            choice=input('> ').strip()
        except (EOFError, KeyboardInterrupt):
            print('\nВыход из Server Manager')
            break
        try:
            if choice=='0':break
            if choice=='1':
                for s in list_sites(AVAILABLE,ENABLED):print(_display_site(s))
            elif choice=='2':_create_site(runner,state,dry_run)
            elif choice=='3':_site_action('disable',runner,state,dry_run)
            elif choice=='4':_site_action('enable',runner,state,dry_run)
            elif choice=='5':_systemd_menu(runner,state,dry_run)
            elif choice=='6':_compose_menu(runner,state,dry_run)
            elif choice=='7':_certbot_menu(runner,state,dry_run)
            elif choice=='8':print(format_listeners(list_listeners(runner)))
            elif choice=='9':print(runner.run(['nginx','-t']).stderr)
            elif choice=='10':
                sub=input('1 — журнал, 2 — восстановить backup Nginx: ').strip()
                if sub=='2':_restore_backup(runner,state,dry_run)
                elif sub=='1':
                    path=state.root/'audit.jsonl'
                    print('\n'.join(path.read_text().splitlines()[-15:]) if path.exists() else 'Журнал пуст')
        except (ValueError,RuntimeError,OSError) as exc:print('Ошибка:',exc)

class DryRunState:
    """No persistence or privileged directory creation in dry-run mode."""
    root=Path('/nonexistent/server-manager-dry-run')
    def record(self,*args,**kwargs):pass
    def save_state(self,*args,**kwargs):pass
    def load_state(self):return {}

def main(argv=None,runner=None):
    parser=argparse.ArgumentParser(
        prog='server-manager',
        description='Server Manager для Debian. Без аргументов запускается интерактивное меню.',
        epilog='Примеры: sudo python3 server-manager.py; sudo server-manager; server-manager ports',
    )
    parser.add_argument('--dry-run',action='store_true')
    sub=parser.add_subparsers(dest='command')
    port=sub.add_parser('ports');port.add_argument('--tcp',action='store_true');port.add_argument('--udp',action='store_true')
    sites=sub.add_parser('sites');sites.add_argument('action',choices=['list'])
    args=parser.parse_args(argv)
    runner=runner or CommandRunner()
    try:
        if args.command=='ports':
            protocol='tcp' if args.tcp and not args.udp else 'udp' if args.udp and not args.tcp else None
            print(format_listeners(list_listeners(runner,protocol)));return 0
        if args.command=='sites':
            for site in list_sites(AVAILABLE,ENABLED):print(_display_site(site))
            return 0
        menu(runner,DryRunState() if args.dry_run else StateStore(),args.dry_run)
        return 0
    except (RuntimeError,OSError) as exc:
        print('Ошибка:',exc);return 1
