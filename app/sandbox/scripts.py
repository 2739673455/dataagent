"""沙箱内受控文件操作脚本"""

_SHELL_JOB_STARTED_MARKER = "__DATAAGENT_SHELL_JOB_STARTED__"

_SHELL_JOB_WRAPPER_SCRIPT = r"""
import base64
import json
import os
import secrets
import signal
import stat
import subprocess
import sys

payload = json.loads(base64.b64decode(sys.argv[1]).decode())
workspace = payload["workspace"]
staging = payload["staging"]
job_id = payload["job_id"]
command = payload["command"]
owner_uid = int(payload["owner_uid"])
owner_gid = int(payload["owner_gid"])
file_mode = int(payload["file_mode"])
directory_mode = int(payload["directory_mode"])
max_file_bytes = int(payload["max_file_bytes"])
control_name = f"{job_id}.json"
control_fd = None
log_fd = None
process = None


def open_owned_directory(parent_fd, name):
    try:
        os.mkdir(name, mode=directory_mode, dir_fd=parent_fd)
    except FileExistsError:
        pass
    child_fd = os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=parent_fd,
    )
    child_stat = os.fstat(child_fd)
    if not stat.S_ISDIR(child_stat.st_mode):
        raise OSError("shell job log directory is invalid")
    if child_stat.st_uid != owner_uid or child_stat.st_gid != owner_gid:
        os.fchown(child_fd, owner_uid, owner_gid)
    os.fchmod(child_fd, directory_mode)
    return child_fd


def write_control(data):
    temporary_name = f".{job_id}.{secrets.token_hex(8)}.tmp"
    temporary_fd = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=control_fd,
    )
    try:
        content = json.dumps(data, separators=(",", ":")).encode()
        view = memoryview(content)
        while view:
            count = os.write(temporary_fd, view)
            view = view[count:]
    finally:
        os.close(temporary_fd)
    os.replace(
        temporary_name,
        control_name,
        src_dir_fd=control_fd,
        dst_dir_fd=control_fd,
    )


def demote_child():
    os.setgroups([])
    os.setgid(owner_gid)
    os.setuid(owner_uid)
    os.umask(int(payload["umask"]))


def process_group_alive(pgid):
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            with open(f"/proc/{entry.name}/stat", "r") as stat_file:
                stat_line = stat_file.read()
            fields = stat_line[stat_line.rfind(")") + 2:].split()
            if len(fields) >= 3 and int(fields[2]) == pgid and fields[0] != "Z":
                return True
        except (FileNotFoundError, PermissionError, ValueError):
            continue
    return False


try:
    workspace_fd = os.open(
        workspace,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        workspace_stat = os.fstat(workspace_fd)
        if (
            not stat.S_ISDIR(workspace_stat.st_mode)
            or workspace_stat.st_uid != owner_uid
            or workspace_stat.st_gid != owner_gid
        ):
            raise PermissionError("shell job workspace owner is invalid")
        large_results_fd = open_owned_directory(workspace_fd, "large_tool_results")
        try:
            logs_fd = open_owned_directory(large_results_fd, "shell_jobs")
            try:
                log_fd = os.open(
                    f"{job_id}.log",
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                    file_mode,
                    dir_fd=logs_fd,
                )
                os.fchown(log_fd, owner_uid, owner_gid)
                os.fchmod(log_fd, file_mode)
            finally:
                os.close(logs_fd)
        finally:
            os.close(large_results_fd)
    finally:
        os.close(workspace_fd)

    control_fd = os.open(
        staging,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        os.mkdir("shell_jobs", mode=0o700, dir_fd=control_fd)
    except FileExistsError:
        pass
    shell_control_fd = os.open(
        "shell_jobs",
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=control_fd,
    )
    os.close(control_fd)
    control_fd = shell_control_fd
    control_stat = os.fstat(control_fd)
    if control_stat.st_uid != 0 or control_stat.st_gid != 0:
        raise PermissionError("shell job control directory owner is invalid")
    os.fchmod(control_fd, 0o700)

    environment = os.environ.copy()
    process = subprocess.Popen(
        ["/bin/sh", "-lc", command],
        cwd=workspace,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        preexec_fn=demote_child,
    )
    write_control({"status": "running", "pgid": process.pid})
    print("__DATAAGENT_SHELL_JOB_STARTED__", flush=True)
    retained = 0
    output_truncated = False
    while True:
        chunk = process.stdout.read(65536)
        if not chunk:
            break
        remaining = max_file_bytes - retained
        if remaining > 0:
            written = chunk[:remaining]
            view = memoryview(written)
            while view:
                count = os.write(log_fd, view)
                view = view[count:]
            retained += len(written)
        if len(chunk) > max(remaining, 0):
            output_truncated = True
    exit_code = process.wait()
    while process_group_alive(process.pid):
        import time
        time.sleep(0.05)
    write_control(
        {
            "status": "finished",
            "pgid": process.pid,
            "exit_code": exit_code,
            "output_truncated": output_truncated,
        }
    )
except BaseException as error:
    if control_fd is not None:
        try:
            write_control(
                {
                    "status": "failed",
                    "pgid": process.pid if process is not None else None,
                    "exit_code": None,
                    "output_truncated": False,
                    "error": f"{type(error).__name__}: {error}",
                }
            )
        except BaseException:
            pass
    if process is not None and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    sys.exit(125)
finally:
    if log_fd is not None:
        os.close(log_fd)
    if control_fd is not None:
        os.close(control_fd)

sys.exit(exit_code if 0 <= exit_code <= 255 else 128 + abs(exit_code))
""".strip()


_CANCEL_SHELL_JOB_SCRIPT = r"""
import json
import os
import signal
import sys
import time

control_path = sys.argv[1]
grace_seconds = float(sys.argv[2])


def process_group_alive(pgid):
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            with open(f"/proc/{entry.name}/stat", "r") as stat_file:
                stat_line = stat_file.read()
            fields = stat_line[stat_line.rfind(")") + 2:].split()
            if len(fields) >= 3 and int(fields[2]) == pgid and fields[0] != "Z":
                return True
        except (FileNotFoundError, PermissionError, ValueError):
            continue
    return False


try:
    control_fd = os.open(control_path, os.O_RDONLY | os.O_NOFOLLOW)
except FileNotFoundError:
    print(json.dumps({"ready": False, "signal_sent": False, "exited": False}))
    sys.exit(0)
try:
    with os.fdopen(control_fd, "r") as control_file:
        state = json.load(control_file)
except (OSError, ValueError):
    print(json.dumps({"ready": False, "signal_sent": False, "exited": False}))
    sys.exit(0)

if state.get("status") != "running":
    print(json.dumps({"ready": True, "signal_sent": False, "exited": True}))
    sys.exit(0)
pgid = state.get("pgid")
if not isinstance(pgid, int) or pgid <= 1:
    print(json.dumps({"ready": False, "signal_sent": False, "exited": False}))
    sys.exit(0)

signal_sent = False
try:
    os.killpg(pgid, signal.SIGTERM)
    signal_sent = True
except ProcessLookupError:
    print(json.dumps({"ready": True, "signal_sent": False, "exited": True}))
    sys.exit(0)

deadline = time.monotonic() + grace_seconds
while time.monotonic() < deadline:
    if not process_group_alive(pgid):
        print(json.dumps({"ready": True, "signal_sent": signal_sent, "exited": True}))
        sys.exit(0)
    time.sleep(0.05)

try:
    os.killpg(pgid, signal.SIGKILL)
    signal_sent = True
except ProcessLookupError:
    pass
deadline = time.monotonic() + grace_seconds
while time.monotonic() < deadline:
    if not process_group_alive(pgid):
        print(json.dumps({"ready": True, "signal_sent": signal_sent, "exited": True}))
        sys.exit(0)
    time.sleep(0.05)
print(json.dumps({"ready": True, "signal_sent": signal_sent, "exited": False}))
""".strip()

_COMMIT_UPLOAD_SCRIPT = """
import base64
import json
import os
import stat
import sys

payload = json.loads(base64.b64decode(sys.argv[1]).decode())
root = payload["root"]
source = payload["source"]
owner_uid = int(payload["owner_uid"])
owner_gid = int(payload["owner_gid"])
file_mode = int(payload["file_mode"])
directory_mode = int(payload["directory_mode"])
parts = payload["relative_target"].split("/")
source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
try:
    if not stat.S_ISREG(os.fstat(source_fd).st_mode):
        raise OSError("暂存文件无效")
    os.fchown(source_fd, owner_uid, owner_gid)
    os.fchmod(source_fd, file_mode)
finally:
    os.close(source_fd)
root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
directory_fd = os.dup(root_fd)
try:
    root_stat = os.fstat(root_fd)
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_uid != owner_uid
        or root_stat.st_gid != owner_gid
    ):
        raise PermissionError("工作区所有者无效")
    for component in parts[:-1]:
        created = False
        try:
            os.mkdir(component, mode=directory_mode, dir_fd=directory_fd)
            created = True
        except FileExistsError:
            pass
        next_fd = os.open(
            component,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        if created:
            os.fchown(next_fd, owner_uid, owner_gid)
            os.fchmod(next_fd, directory_mode)
        next_stat = os.fstat(next_fd)
        if next_stat.st_uid != owner_uid or next_stat.st_gid != owner_gid:
            raise PermissionError("目标目录所有者无效")
        os.close(directory_fd)
        directory_fd = next_fd
    os.replace(
        source,
        parts[-1],
        src_dir_fd=None,
        dst_dir_fd=directory_fd,
    )
finally:
    os.close(directory_fd)
    os.close(root_fd)
""".strip()

_LARGE_EDIT_SCRIPT = """
import base64
import json
import os
import stat
import sys

payload = json.loads(base64.b64decode(sys.argv[1]).decode())
try:
    with open(payload["old"], "rb") as old_file:
        old = old_file.read().decode("utf-8")
    with open(payload["new"], "rb") as new_file:
        new = new_file.read().decode("utf-8")
    target_stat = os.stat(payload["target"])
    if not stat.S_ISREG(target_stat.st_mode):
        print(json.dumps({"error": "not_a_file"}))
        sys.exit(0)
    with open(payload["target"], "rb") as target_file:
        content = target_file.read().decode("utf-8")

    old_crlf = old.replace("\\r\\n", "\\n").replace("\\n", "\\r\\n")
    old_lf = old.replace("\\r\\n", "\\n")
    new_crlf = new.replace("\\r\\n", "\\n").replace("\\n", "\\r\\n")
    new_lf = new.replace("\\r\\n", "\\n")
    count = 0
    matched_old, matched_new = old, new
    for candidate_old, candidate_new in (
        (old, new),
        (old_crlf, new_crlf),
        (old_lf, new_lf),
    ):
        candidate_count = content.count(candidate_old)
        if candidate_count:
            matched_old = candidate_old
            matched_new = candidate_new
            count = candidate_count
            break
    if count == 0:
        print(json.dumps({"error": "string_not_found"}))
    elif count > 1 and not payload["replace_all"]:
        print(json.dumps({"error": "multiple_occurrences", "count": count}))
    else:
        updated = (
            content.replace(matched_old, matched_new)
            if payload["replace_all"]
            else content.replace(matched_old, matched_new, 1)
        )
        updated_bytes = updated.encode("utf-8")
        if len(updated_bytes) > payload["max_file_bytes"]:
            print(json.dumps({"error": "file_too_large"}))
            sys.exit(0)
        workspace_size = 0
        for current_root, _, files in os.walk(payload["workspace"]):
            for name in files:
                current_path = os.path.join(current_root, name)
                try:
                    if os.path.isfile(current_path) and not os.path.islink(current_path):
                        workspace_size += os.path.getsize(current_path)
                except OSError:
                    pass
        projected_size = len(updated_bytes) + workspace_size - len(content.encode("utf-8"))
        if projected_size > payload["max_workspace_bytes"]:
            print(json.dumps({"error": "workspace_limit_exceeded"}))
            sys.exit(0)
        with open(payload["target"], "wb") as target_file:
            target_file.write(updated_bytes)
        print(json.dumps({"count": count}))
except FileNotFoundError:
    print(json.dumps({"error": "file_not_found"}))
except PermissionError:
    print(json.dumps({"error": "permission_denied"}))
except UnicodeDecodeError:
    print(json.dumps({"error": "not_a_text_file"}))
finally:
    for path in (payload["old"], payload["new"]):
        try:
            os.remove(path)
        except OSError:
            pass
""".strip()
