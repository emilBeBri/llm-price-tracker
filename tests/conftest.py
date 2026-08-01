

def pytest_terminal_summary(terminalreporter, exitstatus, config):
    failed = terminalreporter.stats.get('failed', [])
    if failed:
        text = "\n\n".join([rep.longreprtext for rep in failed])
        copied = False
        # Try pyperclip first
        try:
            import pyperclip
            pyperclip.copy(text)
            copied = True
        except Exception:
            pass
        # Fallbacks
        if not copied:
            import sys
            import subprocess
            import platform
            plat = platform.system()
            try:
                if plat == "Linux":
                    # Try xclip
                    subprocess.run(['xclip', '-selection', 'clipboard'], input=text.encode(), check=True)
                    copied = True
                elif plat == "Darwin":
                    # macOS pbcopy
                    subprocess.run(['pbcopy'], input=text.encode(), check=True)
                    copied = True
                elif plat == "Windows":
                    # Windows clip
                    subprocess.run(['clip'], input=text.encode(), check=True)
                    copied = True
            except Exception:
                pass
        if not copied:
            print("\n[pytest] Could not copy failure output to clipboard (no clipboard utility found).")


