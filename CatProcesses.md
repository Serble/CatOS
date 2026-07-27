# CatOS: processes, user mode, and an interactive shell

Roadmap from the current state (VFS + CatFS + drivers, everything running in
kernel mode) to the goal:

> **A bootable OS that opens to a shell where commands can be typed and run
> user-mode applications (`ls`, `touch`, `echo`, `cat`, ...) that talk to the
> kernel only through syscalls.**

This document is the design + step plan. It records *what the VM already gives
us for free*, the pieces we must build, the ABIs between them, and a phased
order with per-phase verification.

---

## 1. What the hardware already provides (no VM changes needed for these)

The CatMachine VM is already a protected-mode machine. Confirmed from the VM
source (`CatMachine/CatVM/`):

- **Four privilege modes** via the `Mode` byte (`CatCpuState.cs`): bit0 =
  VirtualMode, bit1 = SupervisorMode. `0b00` Kernel, `0b01` User, `0b10`
  Supervisor, `0b11` Driver.
- **`syscall` (opcode 0x59) is the only non-privileged trap.** `int`, `di`,
  `ei`, `in`, `out`, `iret`, `setit`, `setksp`/`getksp` all call
  `TryPrivileged()` and raise a **ProtectionFault (0x03)** from user mode
  (`IntOperation.cs`, `SerialOperation.cs`, etc.). So a user process *cannot*
  touch hardware or interrupts — it must go through `syscall`. That is real
  isolation, for free.
- **Segment memory protection** (`CatVm.Translate`): in virtual mode every
  guest access is `phys = addr + MBase` after a bounds check `addr + size <=
  MLen`; an out-of-range access raises **PageFault (0x00)**. Each process gets a
  contiguous physical window `[MBase, MBase+MLen)` and sees it as `0..MLen`.
  `MBase`/`MLen` are **never writable by an instruction** — they change only
  when `iret` pops them from an interrupt frame. Address-space switching is
  therefore done by building/among frames, not by a "load segment" op.
- **Trap frames + `Ksp`** (`CatVm.BuildInterruptFrameAndDispatch` / `Iret`): on
  a **user→kernel** trap the CPU sets `Mode=0`, switches `Sp` to the fixed
  per-process kernel-stack base `Ksp`, and pushes the *entire* resumable state,
  low→high: `r0..r7, MLen, MBase, Fl, userSp, userIp`, then a 1-byte **marker**
  (`0x01` user, `0x02` supervisor/driver). On a **kernel→kernel** trap it pushes
  only `Ip` + marker `0x00`. `iret` pops the marker and restores exactly what
  was pushed, atomically, and restores `Mode` from the marker. `setksp` sets the
  per-process `Ksp`.
- **A preemption timer** already runs: `timer.cat` drives interrupt `0x71`
  periodically. The scheduler hooks straight into it.
- **Contiguous physical allocation**: `alloc.cat` `allocpages(n)` returns a
  contiguous run — exactly what a process segment and kernel stack need.
- **The syscall dispatcher already handles the user-frame return path**
  (`syscall.cat`): for a user caller it patches the *saved* `r0` on the kernel
  stack (frame offset `SYSFRAME_R0 = 49`) so the value survives `iret`.

**Consequence:** we do not need to invent context-switching, privilege, or
memory protection. We need to *drive* them: allocate segments, build frames,
switch on the timer, and expose a syscall surface.

### Key hardware gotchas to design around

- **No auto-DI on trap entry.** The CPU does not disable interrupts when a
  handler runs. Handlers that must not nest have to `di` themselves. (`in`/`out`
  to the timer/console are fine mid-handler since we're in kernel mode.)
- **Interrupt table is read at physical addresses** — user mode can't redirect
  it. It stays in kernel space (already the case in `ints.cat`).
- **A syscall/interrupt handler runs in kernel mode (identity-mapped).** It
  cannot dereference a user pointer directly; it must add the faulting process's
  `MBase` and bounds-check against `MLen`. → we need **copyin/copyout**.

---

## 2. What we must build

### 2.1 The one VM-side addition: an input device (chosen approach)

The VM can already *print* (the `SerialMonitor` device, type `0xBBAC8C8C`:
`out <port>, <char>` writes one char to host stdout). It has **no terminal
keyboard**. The only existing keyboard is the Raylib GUI window's
`KeyboardInputDevice` (type `0x2EB3AD76`): it fires an interrupt (default
`0x70`, remappable via `out`) and delivers `[eventType, keycode]` pairs read
with two `in`s (`uint.MaxValue` when the queue is empty).

Decision (per project owner): **the kernel gets a device-agnostic input-driver
framework** so it works with *any* input hardware. Backends:

1. **A new `StdinSerialDevice` (C#, in CatMachine)** — modeled on
   `ConsoleSerialMonitor` + the `InputDevice` base. It reads host terminal stdin
   on a background thread and, per byte, enqueues `[1, asciiChar]` and raises its
   interrupt code (default `0x70`). This gives a real "type in the terminal"
   console *now*. One small `[CommandLineConstructable("Stdin", ...)]` file +
   nothing else. It deliberately uses the **same interrupt+`[type,value]`
   protocol as the Raylib keyboard** so a single kernel driver serves both.
2. **The Raylib `KeyboardInputDevice`** — already exists; the same kernel input
   driver binds to it (different device type, values are raw keycodes → run them
   through a keymap).

The framework must make both interchangeable; the Stdin device is the default
for headless/terminal use.

### 2.2 Kernel input framework + tty (`input.cat`, `console.cat`)

- **Console output driver**: bind on the `SerialMonitor` type via
  `drvregister`; publish `/dev/console` as a `NODE_CHARDEV` whose `OP_WRITE`
  emits each byte with `out port, byte`. A `kputs`/`kputc` kernel helper writes
  through it (retire the `int 0x90`/`int 0x80` debug prints, which are marked
  REMOVE in the VM).
- **Input core**: a ring buffer + minimal line discipline (echo typed chars,
  handle backspace, translate CR→LF, line-buffered delivery). Backend drivers
  call `input_push_byte(c)`; that's the single seam every input device feeds.
- **Input backend driver(s)**: bind on the input device type, register the
  input interrupt (`0x70`), and in the ISR drain `[type,value]` and call
  `input_push_byte` (ASCII directly for Stdin; keymap for Raylib).
- **`/dev/console` read side**: `OP_READ` returns buffered input; if none is
  available it **blocks** the calling process (see 2.5) until a line arrives;
  the input ISR wakes blocked readers.
- fd 0/1/2 of every process map to `/dev/console`.

### 2.3 Process model (`proc.cat`)

**PCB** (heap-allocated struct; kept in a fixed table or list). Fields:

| field | meaning |
|---|---|
| `PID` | process id |
| `STATE` | 0 free, 1 ready, 2 running, 3 blocked, 4 zombie |
| `KSTACK_BASE` | kernel-stack page base (for freeing) |
| `KSP` | fixed kernel-stack top loaded into the `Ksp` register when scheduled |
| `SAVED_SP` | kernel `Sp` at suspension — points at the process's trap frame; the resume point |
| `MBASE`,`MLEN` | user segment window |
| `USEG_BASE`,`USEG_PAGES` | user segment (for freeing) |
| `PARENT` | parent PCB |
| `PPID` | parent's pid, taken at creation — the pointer above can outlive the slot it names, so anything reporting a parent (`ps`) uses this |
| `EXITCODE` | set on exit, read by `wait` |
| `WAITCHAN` | what this proc is blocked on (0 = not blocked) |
| `FDTABLE[N]` | per-process map of small fd → VFS handle index (−1 free) |
| `NAME` | program name (its path's last component), stored inline rather than as a pointer so it survives the caller's scratch buffers; `ps` reads it through `SYS_PSLIST` |
| `NEXT` | ready-queue / list link |

**Per-process resources:** a user segment via `allocpages` (image + data +
stack; `MBase` = phys base, `MLen` = size, user `sp` starts at `MLen`) and a
kernel stack via `allocpage` (`KSP` = top).

**Per-process fd tables** are new: the VFS handle table (`vfshandles`) is
currently a single global array. The PCB `FDTABLE` indexes into it; `exec`
inherits/duplicates fd 0/1/2.

### 2.4 Scheduler + context switch (`sched.cat`)

Round-robin over a ready queue, driven by the timer and by voluntary syscalls
(`yield`, `exit`, blocking `read`/`wait`).

**Switch mechanism (fits this VM exactly):** a suspended process is fully
captured by `SAVED_SP` (the top of its trap frame on its own kernel stack).

- On timer `0x71`: the ISR first reads the **frame marker** at `[sp]`.
  - **marker = kernel (0x00):** we preempted kernel code (regs not saved by hw).
    Keep the kernel **non-preemptible** for v1 — `int_prologue`, re-arm, don't
    switch, `int_epilogue`. (Kernel code runs to its next drop-to-user; simple
    and safe.)
  - **marker = user (0x01):** the user's regs are already in the frame, so the
    ISR may freely use registers. **Capture `SAVED_SP = Sp` immediately, before
    any push/`call`.** Re-arm the timer, then `schedule()`.
- `schedule()`: store `SAVED_SP` into the outgoing PCB; pick the next ready PCB;
  then resume it uniformly:

  ```
  Sp = next.SAVED_SP        ; discard our transient kernel stack
  setksp next.KSP           ; next trap from this proc lands here
  iret                      ; pops next's frame -> runs next in user mode
  ```

- **Voluntary switch** (a `syscall` that yields/exits/blocks): the syscall
  handler entered from user mode has the same full frame, so it captures
  `SAVED_SP` the same way and calls the same resume path. `exit` marks the
  caller zombie and never resumes it; `yield` re-queues it; blocking `read`/
  `wait` marks it blocked.
- **First start of a process:** manufacture a synthetic **user frame** on its
  kernel stack (`r0..r7 = 0`, `MLen`/`MBase` = its segment, `Fl = 0`,
  `userSp = MLen`, `userIp = entry`, marker `0x01`), set `SAVED_SP` to it. The
  normal resume path then enters it. This makes start and resume identical.
- **Idle task:** to avoid a no-runnable deadlock (e.g. shell blocked in `wait`
  while its child blocks in `read`), run a trivial `idle` user process that
  loops on `SYS_YIELD`; it is always ready and never blocks/exits.

### 2.5 Blocking / wakeup

Minimal primitive: `block(chan)` sets `STATE=blocked`, `WAITCHAN=chan`, and
reschedules; `wakeup(chan)` flips every process blocked on `chan` back to ready.
Channels are just tags (e.g. the console node address for readers, a child PID
for `wait`). Used by `/dev/console` read and by `SYS_WAIT`.

### 2.6 Syscall surface (`syscall.cat`, extended)

Convention unchanged: `r1` = syscall number, `r2/r3/...` = args, `r0` = result,
returned across `iret` via the existing frame-patch. **All user pointers are
translated with copyin/copyout** (add current `MBase`, bound-check `MLen`).

Initial set (enough for the shell + coreutils):

| # | name | args | returns |
|---|------|------|---------|
| | `SYS_EXIT` | code | (no return) |
| | `SYS_YIELD` | – | 0 |
| | `SYS_WRITE` | fd, buf, len | bytes written |
| | `SYS_READ` | fd, buf, len | bytes read (blocks) |
| | `SYS_OPEN` | path, mode | fd or −1 |
| | `SYS_CLOSE` | fd | 0/−1 |
| | `SYS_READDIR` | fd, index, buf | 1 + name, or 0 at end |
| | `SYS_CREATE` | path, type | 0/−1 (files & dirs → `touch`, `mkdir`) |
| | `SYS_REMOVE` | path | 0/−1 (`rm`) |
| | `SYS_SPAWN` | path, argv | child pid or −1 |
| | `SYS_WAIT` | pid | child exit code (blocks) |
| | `SYS_STAT` | path, buf | 0/−1 (size/type; for `ls -l`, `cat`) |

`copyin`/`copyout`/`copyinstr` kernel helpers are mandatory building blocks
here.

### 2.7 Executable format + loader (`exec.cat`)

- **v1: flat binary.** The image is loaded at user vaddr `0`; entry is offset
  `0`. Apps are assembled exactly like the kernel but *linked to run at 0* — and
  since virtual mode relocates by `MBase`, absolute-from-0 addresses are the
  correct user addresses. Data/strings (absolute from 0) and a stack at the top
  of the segment work unchanged.
- Loader: `allocpages` a segment sized to `filesize + bss + stack`, read the
  file via the VFS into it (through the CatFS driver), zero the bss/stack,
  build the initial user frame, enqueue the PCB.
- **argv convention:** the loader copies the arg strings and a `char*` array to
  the top of the user stack and starts the app with `r1 = argc`,
  `r2 = argv` (user address); user `sp` just below. A tiny crt reads them.
- *Deferred:* a real header (magic, entry, bss size, symbol table) for larger
  programs; dynamic loading. Flat + fixed conventions are enough for v1.

### 2.8 Userland (`user/`)

- **crt / mini-libc** (`user/crt.cat`, `user/sys.cat`): `_start` (reads
  `r1/r2` → `argc/argv`, calls `main`, then `SYS_EXIT`), syscall stub macros,
  and helpers (`puts`, `getline`, `strcmp`, number formatting). Shared by every
  app.
- **`sh`**: print a prompt to fd 1, `getline` from fd 0, tokenise into
  `argv`, resolve the command to `/bin/<name>`, `SYS_SPAWN` + `SYS_WAIT`, loop.
  A couple of built-ins (`cd`, `exit`).
- **coreutils**: `echo` (write argv to fd 1), `ls` (open dir, `SYS_READDIR`
  loop), `touch` (`SYS_CREATE` file), `cat` (open file, read→write to fd 1),
  plus `mkdir`, `rm` for free from the same syscalls.

### 2.9 init + filesystem layout

- **`init`** = the first user process the kernel starts. It spawns `sh` and
  respawns it if it exits.
- Apps live at **`/bin`** on the persistent CatFS partition. Extend the host
  tooling (`tools/mkfs_catfs.py` / a small `tools/catfs_put.py` built on
  `catfs.py`) to import the assembled flat binaries into `/bin` when the disk
  image is built. `packandrun.sh` gains: assemble each `user/*.cat` → flat bin,
  populate `/bin`, add `-d SerialMonitor` (and `-d Stdin`).
- **Done:** the on-disk CatFS is now mounted as the literal root `/` (the disk
  is brought up before the root mount), and `/bin`, `/dev`, `/sys` are created on
  it by the host tools (`tools/catfs_import.py`, driven by `packandrun.sh`). The
  user-mode programs are installed into `/bin` on the drive, so the kernel loads
  idle/init and everything they spawn straight off the disk. `./packandrun.sh
  --keep` reuses the drive (and its `/bin`) across reboots.

---

## 3. Phased plan (each phase is independently verifiable)

**Phase 0 — Real console output.**
Add `-d SerialMonitor` to `packandrun.sh`. Write the console output driver +
`/dev/console` chardev + `kputc`/`kputs`; convert the boot demos to print real
text. *Verify:* boot prints readable text to the terminal via the device (not
`int 0x90`).

**Phase 1 — Input framework + tty.**
Add the C# `StdinSerialDevice` to CatMachine and `-d Stdin` to the launcher.
Write the kernel input core (ring + line discipline), the input backend driver
(ISR → `input_push_byte`), and the `/dev/console` read/block path. Stub the
Raylib keyboard backend to prove device-agnosticism. *Verify:* a temporary
kernel loop reads a line from `/dev/console` and echoes it back.

**Phase 2 — Process + scheduler machinery.**
PCB, ready queue, segment + kernel-stack allocation, the frame-swap context
switch, timer-driven preemption (user-frame only), `yield`, `block`/`wakeup`,
idle task, and "start first user process". *Verify:* the kernel builds one user
process that runs a hand-written flat blob (just `SYS_EXIT`), and the timer
preempts a two-process spin test that alternates output.

**Phase 3 — User mode + syscalls + exec.**
`copyin`/`copyout`, the syscall set in 2.6, per-process fd tables, the flat-
binary loader + argv, `SYS_SPAWN`/`SYS_WAIT`. crt + `sys.cat`. *Verify:* a
user `hello` loaded from CatFS runs in **user mode** (confirm a privileged op
faults), writes via `SYS_WRITE`, exits with a code the parent reads via
`SYS_WAIT`.

**Phase 4 — Shell + coreutils.**
`sh`, `echo`, `ls`, `touch`, `cat` (+`mkdir`,`rm`). Build step to assemble
userland and populate `/bin` on the CatFS image. *Verify:* boot → prompt; type
`echo hi` → `hi`; `touch f` then `ls` shows `f`; `cat` a file prints it;
unknown command reports an error.

**Phase 5 — init + polish.**
`init` spawns/respawns `sh`; exit codes and `$?`; path search; teardown frees
segments/kernel-stacks/fds and `wait` reaps zombies. Optional: `ps`, a
canned-stdin automated boot test, and flipping `/` to CatFS.

---

## 4. "Anything else we need" — checklist of easy-to-miss pieces

- **copyin/copyout/copyinstr** — user pointers are unusable raw in a handler.
- **Per-process fd tables** — VFS handles are global today.
- **Idle task** — or the machine deadlocks when everything blocks.
- **Kernel non-preemption (v1)** — only switch on user-mode preemption; avoids
  the two-stack context-switch complexity.
- **DI/EI discipline in handlers** — the CPU does not auto-disable interrupts on
  trap entry.
- **Blocking/wakeup** — needed for console `read` and `wait`.
- **Process teardown + zombie reaping** — free segment, kernel stack, fds.
- **A userland runtime (crt + syscall stubs)** — every app links it.
- **argv/exit-code convention** — pin it once, in the loader and crt.
- **The VM stdin device** — the only change outside the CatOS repo.
- **Retire `int 0x90`/`int 0x80`** debug prints in favour of `/dev/console`.

---

## 5. Deferred (explicitly out of scope for the shell milestone)

- A real executable header, dynamic loading, shared libraries.
- Demand paging / growable heaps for user processes (segment is fixed-size v1).
- Fork/copy-on-write (we use spawn = load-fresh, which is simpler and enough).
- Signals, job control, pipes, redirection (natural next step after the shell).
- Multi-core, priorities beyond round-robin.
- CatFS as the literal root `/` (separate, already-scoped task).
- A C toolchain for userland (CatLLVM exists — a possible future path; v1 is
  Cat assembly, consistent with the rest of the OS).
