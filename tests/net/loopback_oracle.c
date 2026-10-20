/* 5.2 oracle. The same loopback round trip as tests/net/loopback.py, issued as
 * the identical raw syscalls (no libc socket wrappers), so its stdout is an
 * independent reference for Lithon's. In particular the address is built with
 * htonl -- the standard correct byte order -- rather than Lithon's hand-rolled
 * swap32, so the two agree only if the hand-rolled swap is right.
 *
 * Build/run is handled by tools/check_net_loopback.py.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>

int main(void) {
    struct sockaddr_in sa;
    memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET;                             /* 2 */
    sa.sin_port = htons(0);                              /* ephemeral */
    sa.sin_addr.s_addr = htonl(2130706433u);             /* 127.0.0.1 */

    unsigned int socklen = 16;

    int srv = (int)syscall(41, 2, 1, 0);                 /* socket */

    printf("%ld\n", syscall(49, srv, &sa, 16));          /* bind */
    printf("%ld\n", syscall(51, srv, &sa, &socklen));    /* getsockname */
    printf("%u\n", socklen);
    printf("%ld\n", syscall(50, srv, 1));                /* listen */

    int cli = (int)syscall(41, 2, 1, 0);                 /* socket */
    printf("%ld\n", syscall(42, cli, &sa, 16));          /* connect */

    int conn = (int)syscall(43, srv, 0, 0);              /* accept */
    printf("%s\n", conn > 0 ? "True" : "False");

    int64_t out = 123456789;
    int64_t in = 0;
    printf("%ld\n", syscall(44, cli, &out, 8, 0, 0));    /* sendto */
    printf("%ld\n", syscall(45, conn, &in, 8, 0, 0));    /* recvfrom */
    printf("%lld\n", (long long)in);

    printf("%ld\n", syscall(3, cli));                    /* close */
    printf("%ld\n", syscall(3, conn));
    printf("%ld\n", syscall(3, srv));

    /* The failure mode: the same address WITHOUT htonl names 1.0.0.127, so
     * bind fails. Both sides must agree on that too. */
    struct sockaddr_in bad;
    memset(&bad, 0, sizeof bad);
    bad.sin_family = AF_INET;
    bad.sin_port = htons(0);
    bad.sin_addr.s_addr = 2130706433u;                   /* NOT htonl'd */
    int badfd = (int)syscall(41, 2, 1, 0);
    printf("%s\n", syscall(49, badfd, &bad, 16) < 0 ? "True" : "False");
    printf("%ld\n", syscall(3, badfd));
    return 0;
}
