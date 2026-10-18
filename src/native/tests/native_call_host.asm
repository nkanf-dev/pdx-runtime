option casemap:none
EXTERN PdxNativeCall:PROC
PUBLIC PdxTestStackProbe
PUBLIC PdxTestRawReturn
PUBLIC PdxTestNonvolatile

.const
ALIGN 16
raw_xmm QWORD 1122334455667788h, 1020304050607080h
nonvolatile_xmm QWORD 0123456789ABCDEFh, 0FEDCBA9876543210h

.code
; This leaf writes all home slots and checks the first stack argument afterwards.
PdxTestStackProbe PROC
    mov rax, rsp
    and eax, 15
    cmp eax, 8
    jne probe_failed
    mov qword ptr [rsp+8], 11
    mov qword ptr [rsp+16], 22
    mov qword ptr [rsp+24], 33
    mov qword ptr [rsp+32], 44
    cmp qword ptr [rsp+40], 0A5h
    jne probe_failed
    mov eax, 1
    ret
probe_failed:
    xor eax, eax
    ret
PdxTestStackProbe ENDP

PdxTestRawReturn PROC
    mov rax, 0FEDCBA9876543210h
    movdqu xmm0, xmmword ptr raw_xmm
    ret
PdxTestRawReturn ENDP

PdxTestNonvolatile PROC FRAME

    push rbp
    .pushreg rbp
    push rbx
    .pushreg rbx
    push rsi
    .pushreg rsi
    push rdi
    .pushreg rdi
    push r12
    .pushreg r12
    push r13
    .pushreg r13
    push r14
    .pushreg r14
    push r15
    .pushreg r15
    sub rsp, 200
    .allocstack 200
    movdqu [rsp+32], xmm6
    .savexmm128 xmm6, 32
    movdqu [rsp+48], xmm7
    .savexmm128 xmm7, 48
    movdqu [rsp+64], xmm8
    .savexmm128 xmm8, 64
    movdqu [rsp+80], xmm9
    .savexmm128 xmm9, 80
    movdqu [rsp+96], xmm10
    .savexmm128 xmm10, 96
    movdqu [rsp+112], xmm11
    .savexmm128 xmm11, 112
    movdqu [rsp+128], xmm12
    .savexmm128 xmm12, 128
    movdqu [rsp+144], xmm13
    .savexmm128 xmm13, 144
    movdqu [rsp+160], xmm14
    .savexmm128 xmm14, 160
    movdqu [rsp+176], xmm15
    .savexmm128 xmm15, 176
    .endprolog
    mov rbp, 101
    mov rbx, 102
    mov rsi, 103
    mov rdi, 104
    mov r12, 105
    mov r13, 106
    mov r14, 107
    mov r15, 108
    movdqu xmm6, xmmword ptr nonvolatile_xmm
    movdqu xmm7, xmmword ptr nonvolatile_xmm
    movdqu xmm8, xmmword ptr nonvolatile_xmm
    movdqu xmm9, xmmword ptr nonvolatile_xmm
    movdqu xmm10, xmmword ptr nonvolatile_xmm
    movdqu xmm11, xmmword ptr nonvolatile_xmm
    movdqu xmm12, xmmword ptr nonvolatile_xmm
    movdqu xmm13, xmmword ptr nonvolatile_xmm
    movdqu xmm14, xmmword ptr nonvolatile_xmm
    movdqu xmm15, xmmword ptr nonvolatile_xmm
    call PdxNativeCall
    xor r10d, r10d
    cmp rbp, 101
    jne preservation_failed
    cmp rbx, 102
    jne preservation_failed
    cmp rsi, 103
    jne preservation_failed
    cmp rdi, 104
    jne preservation_failed
    cmp r12, 105
    jne preservation_failed
    cmp r13, 106
    jne preservation_failed
    cmp r14, 107
    jne preservation_failed
    cmp r15, 108
    jne preservation_failed
    movdqu xmm0, xmmword ptr nonvolatile_xmm
    movdqa xmm1, xmm6
    pcmpeqb xmm1, xmm0
    pmovmskb eax, xmm1
    cmp eax, 65535
    jne preservation_failed
    movdqa xmm1, xmm7
    pcmpeqb xmm1, xmm0
    pmovmskb eax, xmm1
    cmp eax, 65535
    jne preservation_failed
    movdqa xmm1, xmm8
    pcmpeqb xmm1, xmm0
    pmovmskb eax, xmm1
    cmp eax, 65535
    jne preservation_failed
    movdqa xmm1, xmm9
    pcmpeqb xmm1, xmm0
    pmovmskb eax, xmm1
    cmp eax, 65535
    jne preservation_failed
    movdqa xmm1, xmm10
    pcmpeqb xmm1, xmm0
    pmovmskb eax, xmm1
    cmp eax, 65535
    jne preservation_failed
    movdqa xmm1, xmm11
    pcmpeqb xmm1, xmm0
    pmovmskb eax, xmm1
    cmp eax, 65535
    jne preservation_failed
    movdqa xmm1, xmm12
    pcmpeqb xmm1, xmm0
    pmovmskb eax, xmm1
    cmp eax, 65535
    jne preservation_failed
    movdqa xmm1, xmm13
    pcmpeqb xmm1, xmm0
    pmovmskb eax, xmm1
    cmp eax, 65535
    jne preservation_failed
    movdqa xmm1, xmm14
    pcmpeqb xmm1, xmm0
    pmovmskb eax, xmm1
    cmp eax, 65535
    jne preservation_failed
    movdqa xmm1, xmm15
    pcmpeqb xmm1, xmm0
    pmovmskb eax, xmm1
    cmp eax, 65535
    jne preservation_failed
    jmp restore_registers
preservation_failed:
    mov r10d, 1
restore_registers:
    movdqu xmm6, [rsp+32]
    movdqu xmm7, [rsp+48]
    movdqu xmm8, [rsp+64]
    movdqu xmm9, [rsp+80]
    movdqu xmm10, [rsp+96]
    movdqu xmm11, [rsp+112]
    movdqu xmm12, [rsp+128]
    movdqu xmm13, [rsp+144]
    movdqu xmm14, [rsp+160]
    movdqu xmm15, [rsp+176]
    mov eax, r10d
    add rsp, 200
    pop r15
    pop r14
    pop r13
    pop r12
    pop rdi
    pop rsi
    pop rbx
    pop rbp
    ret
PdxTestNonvolatile ENDP
END
