import unittest
from server_manager.ports import parse_ss, format_listeners


class PortGroupingTests(unittest.TestCase):
    def test_repeated_udp_sockets_show_as_one_port(self):
        line='udp UNCONN 0 0 172.18.0.1:3478 0.0.0.0:* users:(("turnserver",pid=2145631,fd=11))'
        text=format_listeners(parse_ss('\n'.join([line]*18)))
        self.assertEqual(sum('3478' in row for row in text.splitlines()), 1)
        self.assertIn('turnserver', text)
        self.assertIn('2145631', text)

    def test_multiple_addresses_same_port_are_combined(self):
        source='\n'.join([
            'tcp LISTEN 0 128 127.0.0.1:3000 0.0.0.0:* users:(("web",pid=90,fd=1))',
            'tcp LISTEN 0 128 0.0.0.0:3000 0.0.0.0:* users:(("web",pid=90,fd=2))',
        ])
        result=format_listeners(parse_ss(source))
        self.assertEqual(sum('3000' in row for row in result.splitlines()), 1)
        self.assertIn('127.0.0.1', result)
        self.assertIn('0.0.0.0', result)

    def test_tcp_and_udp_remain_distinct(self):
        source='\n'.join([
            'tcp LISTEN 0 128 *:53 *:* users:(("dns",pid=10,fd=1))',
            'udp UNCONN 0 0 *:53 *:* users:(("dns",pid=10,fd=2))',
        ])
        result=format_listeners(parse_ss(source))
        self.assertEqual(sum('53' in row for row in result.splitlines()), 2)

    def test_multiple_owners_on_same_port_are_not_hidden(self):
        source='\n'.join([
            'udp UNCONN 0 0 127.0.0.1:9999 *:* users:(("first",pid=10,fd=1))',
            'udp UNCONN 0 0 127.0.0.1:9999 *:* users:(("second",pid=20,fd=2))',
        ])
        result=format_listeners(parse_ss(source))
        self.assertEqual(sum('9999' in row for row in result.splitlines()), 1)
        self.assertIn('first', result)
        self.assertIn('second', result)


if __name__ == '__main__':
    unittest.main()
