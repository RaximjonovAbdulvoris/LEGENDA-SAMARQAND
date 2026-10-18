"""Linux worker restrictions: no outbound sockets and no core dumps. Fail closed."""
import ctypes
import resource


def restrict_worker():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE
        raise RuntimeError('Cannot restrict OCR dumps')
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise RuntimeError('Cannot restrict OCR privileges')
    lib = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x7fff0000)  # allow other syscalls
    if not context:
        raise RuntimeError('Cannot create OCR syscall filter')
    try:
        # No inherited sockets are passed by the parent. Local socketpair remains
        # available for Python event-loop wakeups; network socket creation is denied.
        for name in (b'socket', b'connect', b'sendto', b'sendmsg', b'sendmmsg'):
            number = lib.seccomp_syscall_resolve_name(name)
            if number < 0 or lib.seccomp_rule_add(context, 0x00050001, number, 0):
                raise RuntimeError('Cannot configure OCR network filter')
        if lib.seccomp_load(context):
            raise RuntimeError('Cannot enable OCR network filter')
    finally:
        lib.seccomp_release(context)
