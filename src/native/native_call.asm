; PdxNativeCallRequest offsets are asserted by native_call.cpp.
option casemap:none
EXTERN __chkstk:PROC
PUBLIC PdxNativeCallAsm

.code
PdxNativeCallAsm PROC FRAME
    push rbp
    .pushreg rbp
    push rbx
    .pushreg rbx
    push rsi
    .pushreg rsi
    sub rsp, 32
    .allocstack 32
    lea rbp, [rsp+32]
    .setframe rbp, 32
    .endprolog

    mov rbx, rcx
    mov rsi, [rbx+24]
    mov rax, [rbx+16]
    cmp rax, 4
    jbe home_only
    sub rax, 4
    shl rax, 3
    add rax, 47                 ; home space + alignment rounding
    and rax, -16
    jmp allocate_outgoing
home_only:
    mov eax, 32
allocate_outgoing:
    ; The fixed frame remains the unwind anchor during dynamic stack allocation.
    call __chkstk
    sub rsp, rax

    mov r10, [rbx+16]
    mov r11d, 4
copy_stack:
    cmp r11, r10
    jae load_registers
    mov rax, [rsi+r11*8]
    mov [rsp+r11*8], rax        ; position 5 starts at outgoing RSP+32
    inc r11
    jmp copy_stack

load_registers:
    xor ecx, ecx
    xor edx, edx
    xor r8d, r8d
    xor r9d, r9d
    pxor xmm0, xmm0
    pxor xmm1, xmm1
    pxor xmm2, xmm2
    pxor xmm3, xmm3
    test r10, r10
    jz invoke_target
    mov rcx, [rsi]
    movq xmm0, rcx
    cmp r10, 1
    je invoke_target
    mov rdx, [rsi+8]
    movq xmm1, rdx
    cmp r10, 2
    je invoke_target
    mov r8, [rsi+16]
    movq xmm2, r8
    cmp r10, 3
    je invoke_target
    mov r9, [rsi+24]
    movq xmm3, r9
invoke_target:
    call qword ptr [rbx+8]
    mov [rbx+32], rax
    movdqu [rbx+40], xmm0

    lea rsp, [rbp]
    pop rsi
    pop rbx
    pop rbp
    ret
PdxNativeCallAsm ENDP
END
