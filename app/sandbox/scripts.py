"""沙箱内受控文件操作脚本"""

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
