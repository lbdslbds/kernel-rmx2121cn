/* SPDX-License-Identifier: MIT */
.section .text.start,"ax",%progbits
.global _start
.type _start,%function
_start:
    mov x29, xzr
    mov x30, xzr
    bl init_main
1:  b 1b
.size _start, . - _start
.section .note.GNU-stack,"",%progbits
