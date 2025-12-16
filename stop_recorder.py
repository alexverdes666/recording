import winreg
import subprocess
import time
import psutil
import sys
import getpass
try:
    import win32api
    import win32security
    import win32con
except ImportError:
    print("Error: pywin32 is required. Install with: pip install pywin32")
    sys.exit(1)

APP_NAME = "WindowsSystemLogAgent"
SCRIPT_NAME = "recorder_enterprise.py"
ADMIN_PASSWORD = "Aa123456!" # CHANGE THIS

def remove_persistence():
    """Removes the registry key to stop auto-start."""
    print("[*] Removing persistence from Registry...")
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                           r"Software\Microsoft\Windows\CurrentVersion\Run", 
                           0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, APP_NAME)
        winreg.CloseKey(key)
        print("    Success: Registry key removed.")
    except FileNotFoundError:
        print("    Info: Registry key not found (already clean).")
    except Exception as e:
        print(f"    Error: {e}")

def enable_debug_privilege():
    """Enables SeDebugPrivilege for the current process."""
    try:
        h_token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), 
                                               win32con.TOKEN_ADJUST_PRIVILEGES | win32con.TOKEN_QUERY)
        luid = win32security.LookupPrivilegeValue(None, win32security.SE_DEBUG_NAME)
        win32security.AdjustTokenPrivileges(h_token, 0, [(luid, win32con.SE_PRIVILEGE_ENABLED)])
        # print("Debug privilege enabled.")
    except Exception as e:
        print(f"Failed to enable debug privilege: {e}")

def force_kill(pid):
    """
    Tries to kill a process using win32api directly.
    """
    try:
        # Open process with TERMINATE rights
        # This will only succeed if the DACL allows it (which we just fixed in unlock_process)
        h_process = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
        win32api.TerminateProcess(h_process, 1)
        win32api.CloseHandle(h_process)
        return True
    except Exception as e:
        print(f"    Win32 Kill failed for PID {pid}: {e}")
        return False

def unlock_process(pid):
    """
    Removes the Deny ACEs from the process DACL to allow termination.
    Requires the process to be owned by the current user or have Admin rights.
    """
    try:
        # Open process with WRITE_DAC and READ_CONTROL permissions
        h_process = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.WRITE_DAC | win32con.READ_CONTROL,
            False, pid
        )
        
        # Get current DACL
        sd = win32security.GetKernelObjectSecurity(h_process, win32security.DACL_SECURITY_INFORMATION)
        dacl = sd.GetSecurityDescriptorDacl()
        
        # We want to remove the specific DENY ACE we added.
        # However, iterating and modifying is complex.
        # A simpler approach for the owner is to just Grant themselves Full Control
        # or Reset the DACL to a default state.
        
        # Method: Create a new DACL that grants the current user Full Control
        # Get current user SID
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        
        new_dacl = win32security.ACL()
        new_dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.PROCESS_ALL_ACCESS, user_sid)
        
        # Set the new DACL
        sd.SetSecurityDescriptorDacl(1, new_dacl, 0)
        win32security.SetKernelObjectSecurity(h_process, win32security.DACL_SECURITY_INFORMATION, sd)
        return True
        
    except Exception as e:
        print(f"    Failed to unlock PID {pid}: {e}")
        return False

def kill_processes():
    """Finds and kills all recorder python processes."""
    print(f"[*] Searching for processes running '{SCRIPT_NAME}'...")
    killed_count = 0
    
    # We collect targets first to avoid modification during iteration issues
    targets = []
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline']
            if cmdline and SCRIPT_NAME in ' '.join(cmdline):
                targets.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    if not targets:
        print("    No running recorder processes found.")
        return

    print(f"    Found {len(targets)} process(es).")
    
    # Password Check
    pwd = getpass.getpass("Enter Admin Password to terminate: ")
    if pwd != ADMIN_PASSWORD:
        print("Incorrect password. Aborting.")
        return

    for proc in targets:
        pid = proc.pid
        print(f"    Targeting PID {pid}...")
        
        # Try to kill normally first (might work if not protected yet)
        try:
            proc.kill()
            killed_count += 1
            print(f"    Killed PID {pid} (was not protected).")
            continue
        except psutil.AccessDenied:
            print(f"    PID {pid} is protected. Attempting to unlock...")
        except Exception:
            pass
            
        # Unlock and Kill
        if unlock_process(pid):
            print(f"    Unlocked PID {pid}. Attempting kill...")
            if force_kill(pid):
                 killed_count += 1
                 print(f"    Successfully killed PID {pid}.")
            else:
                 # Fallback to psutil if win32 fails for some reason
                 try:
                    p = psutil.Process(pid)
                    p.kill()
                    killed_count += 1
                    print(f"    Successfully killed PID {pid} (via psutil).")
                 except Exception as e:
                    print(f"    Failed to kill PID {pid} final attempt: {e}")
        else:
            print(f"    Could not unlock PID {pid}.")

    print(f"    Summary: Killed {killed_count} / {len(targets)} processes.")

def main():
    print("=== Secure Recorder Cleanup Tool ===")
    
    # Try to enable debug privileges to handle stubborn processes
    if 'win32security' in sys.modules:
        enable_debug_privilege()
    
    # We ask for password inside kill_processes only if processes are found,
    # or we can ask upfront. Let's ask inside.
    
    kill_processes()
    
    # Remove persistence *after* killing, or independently?
    # Usually we want to clean everything.
    remove_persistence()
    
    print("=== Done ===")

if __name__ == "__main__":
    # Check for admin rights? 
    # Not strictly necessary if we are the owner, but good practice.
    main()
