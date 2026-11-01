"""Subprocess execution helper shared by all ffmpeg/ffprobe callers."""
import subprocess
import asyncio
from loguru import logger
from typing import Union, Sequence, Tuple

RUN_DEBUG_MODE = False


async def run_command(command: Union[str, Sequence[str]]) -> Tuple[str, str, int]:
    """
    Execute a command and return (stdout, stderr, code).
    - Accepts either a list/tuple (recommended) or a string.
    - Tries asyncio subprocess APIs first (non-blocking). If they are unsupported
      on the current event loop (Windows selectors), falls back to running the
      blocking subprocess in a thread to avoid blocking the loop.
    - Returns rc == 0 as 0, non-zero as 22, exceptions as 99 (to match your existing codes).
    """

    # Normalize & choose shell mode
    is_sequence = isinstance(command, (list, tuple))
    use_shell = not is_sequence  # string -> shell, list -> exec
    cmd_for_log = ' '.join(command) if is_sequence else command

    try:
        if RUN_DEBUG_MODE:
            logger.debug(f"run_command using asyncio subprocess: {cmd_for_log!r}")
        if use_shell:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

        try:
            stdout_bytes, stderr_bytes = await proc.communicate()
        except asyncio.CancelledError:
            # Job cancelled: never leave an orphaned ffmpeg/ffprobe running.
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise

        stdout = stdout_bytes.decode(errors='ignore').strip() if stdout_bytes else ''
        stderr = stderr_bytes.decode(errors='ignore').strip() if stderr_bytes else ''

        rc = proc.returncode
        if rc == 0:
            return stdout, stderr, 0
        else:
            return stdout, stderr, 22

    except Exception as exc:
        # Often on Windows you'll see NotImplementedError / RuntimeError for selector loops.
        if RUN_DEBUG_MODE:
            logger.debug(f"async subprocess failed ({exc!r}), falling back to threaded subprocess.run")

        try:
            def sync_run():
                if is_sequence:
                    # safer: pass list with shell=False
                    return subprocess.run(
                        list(command),
                        shell=False,
                        capture_output=True,
                        text=True,
                        errors='ignore'
                    )
                else:
                    # string -> run in shell (user asked for string)
                    return subprocess.run(
                        command,
                        shell=True,
                        capture_output=True,
                        text=True,
                        errors='ignore'
                    )

            # Python 3.9+: use asyncio.to_thread, otherwise run_in_executor
            try:
                result = await asyncio.to_thread(sync_run)
            except AttributeError:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, sync_run)

            stdout = result.stdout.strip() if result.stdout else ''
            stderr = result.stderr.strip() if result.stderr else ''
            rc = result.returncode
            if rc == 0:
                return stdout, stderr, 0
            else:
                return stdout, stderr, 22

        except Exception as exc2:
            logger.exception(f"Fallback subprocess.run failed for command: {cmd_for_log!r}")
            return '', str(exc2), 99
