# SuperCoder Security & Safety Guardrails

SuperCoder incorporates the enterprise safety architecture from `addon/hooks` and `ops-and-setup` to ensure safe execution in any IDE, terminal, or autonomous agent mode.

---

## 1. Dangerous Command Patterns (Blocked Automatically)

The following patterns represent critical risks and are strictly forbidden:

| Category | Forbidden Patterns | Risk Description |
|---|---|---|
| **Root/Wildcard Deletion** | `rm -rf /`, `rm -rf ~*`, `rm -rf .*`, `Remove-Item -Recurse C:\*`, `del /s /q C:\*` | Destructive wipe of OS or home directory |
| **Privilege Abuse** | `chmod -R 777 /`, `chown -R root /`, `takeown /f * /r` | Global permission breakage and security bypass |
| **System Compromise** | `mkfs.*`, `dd if=/dev/zero of=/dev/sd*`, `diskpart` wipe scripts | Disk destruction / partition zeroing |
| **Denial of Service** | `:(){ :|:& };:`, `while true; do fork; done` | Terminal fork-bombs and CPU starvation |
| **Blind Script Execution** | `curl ... \| bash`, `wget ... \| sh`, `powershell -enc ...` (obfuscated) | Remote code execution without audit |
| **Secret Exfiltration** | `cat ~/.ssh/id_rsa`, `type %USERPROFILE%\.ssh\id_rsa`, printing API keys | Credential exposure |

---

## 2. Terminal & Filesystem Isolation Rules

1. **Scoped Operations**: All file deletions, directory cleanups, or build commands must be scoped to relative subpaths inside the workspace.
2. **Pre-flight Checks**: Before running file manipulation scripts, verify the target path exists and is located within the active repository root.
3. **No Unprompted Git History Rewrites**: Commands like `git push --force`, `git reset --hard`, and `git clean -fdx` require explicit user confirmation.
4. **Child Process Sandboxing**: Background processes must be launched with timeout limits and clean shutdown handlers.

---

## 3. Secret & Credential Handling

- **Never hardcode secrets**: No API keys, passwords, bearer tokens, or connection strings in code or documentation.
- **Environment variables**: Use `.env` with a corresponding `.env.example` template (containing dummy placeholders only).
- **Masking in Logs**: Ensure log outputs automatically redact sensitive keys matching regexes such as `sk-[a-zA-Z0-9]+`, `ghp_[a-zA-Z0-9]+`, `Bearer [a-zA-Z0-9_\-\.]+`.

---

## 4. Secure Code Construction

- **SQL**: Always use parameterized queries or type-safe query builders (e.g. `db.execute("SELECT * FROM users WHERE id = ?", (user_id,))`).
- **Command Execution**: Avoid shell string concatenation; pass arguments as an array (`subprocess.run(["git", "status"], check=True)`).
- **Paths**: Resolve canonical paths and verify they start with the allowed root directory before reading/writing.
- **Serialization**: Avoid unsafe deserialization (e.g. `pickle.loads` on untrusted input, YAML `load` without `SafeLoader`).
