/*
 * This file is open source software, licensed to you under the terms
 * of the Apache License, Version 2.0 (the "License").  See the NOTICE file
 * distributed with this work for additional information regarding copyright
 * ownership.  You may not use this file except in compliance with the License.
 *
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
/*
 * Copyright (C) 2014 Cloudius Systems, Ltd.
 */

#include <seastar/net/ip.hh>
#include <seastar/net/virtio.hh>
#include <seastar/net/tcp.hh>
#include <seastar/net/native-stack.hh>
#include <seastar/core/reactor.hh>
#include <fmt/printf.h>

using namespace seastar;
using namespace net;

struct tcp_test {
    ipv4& inet;
    using tcp = net::tcp<ipv4_traits>;
    tcp::listener _listener;
    struct connection {
        tcp::connection tcp_conn;
        explicit connection(tcp::connection tc) : tcp_conn(std::move(tc)) {}
        void run() {
            // Read packets and echo back in the background.
            (void)tcp_conn.wait_for_data().then([this] {
                auto p = tcp_conn.read();
                if (!p.len()) {
                    (void)tcp_conn.close_write();
                    return;
                }
                fmt::print("read {:d} bytes\n", p.len());
                (void)tcp_conn.send(std::move(p));
                run();
            });
        }
    };
    tcp_test(ipv4& inet) : inet(inet), _listener(inet.get_tcp().listen(10000)) {}
    void run() {
        // Run all connections in the background.
        (void)_listener.accept().then([this] (tcp::connection conn) {
            (new connection(std::move(conn)))->run();
            run();
        });
    }
};

int main(int ac, char** av) {
    native_stack_options opts;

    auto vnet = create_virtio_net_device(opts.virtio_opts, opts.lro);
    interface netif(std::move(vnet));
    ipv4 inet(&netif);
    inet.set_host_address(ipv4_address("192.168.122.2"));
    tcp_test tt(inet);
    (void)engine().when_started().then([&tt] { tt.run(); });
    engine().run();
}


