import time
import threading
import psutil
import os
import sys

class Blocker:
    def __init__(self):
        self.running = False
        self.rules = {
            "domain": [],
            "application": []
        }
        self.lock = threading.Lock()
        self.hosts_path = r"C:\Windows\System32\drivers\etc\hosts"
        
    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        
    def stop(self):
        self.running = False
        if hasattr(self, 'thread'):
            self.thread.join(timeout=1)

    def add_rule(self, rule_type, value):
        with self.lock:
            if value not in self.rules.get(rule_type, []):
                self.rules[rule_type].append(value)
                if rule_type == "domain":
                    self._update_hosts_file()

    def remove_rule(self, rule_type, value):
        with self.lock:
            if value in self.rules.get(rule_type, []):
                self.rules[rule_type].remove(value)
                if rule_type == "domain":
                    self._update_hosts_file()

    def get_rules(self):
        with self.lock:
            return self.rules

    def _loop(self):
        while self.running:
            self._check_applications()
            time.sleep(1) # Check every second

    def _check_applications(self):
        # Application blocking logic
        with self.lock:
            app_rules = self.rules["application"]
        
        if not app_rules:
            return

        for proc in psutil.process_iter(['pid', 'name']):
            try:
                # simple check: if rule value is in process name
                for rule in app_rules:
                    if rule.lower() in proc.info['name'].lower():
                        print(f"Blocking application: {proc.info['name']} (Rule: {rule})")
                        proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

    def _update_hosts_file(self):
        # Domain blocking logic
        # CAUTION: Needs Admin privileges
        # We will read the hosts file, remove our managed entries, and add current ones
        
        try:
            with open(self.hosts_path, 'r') as f:
                lines = f.readlines()
            
            new_lines = []
            in_managed_block = False
            
            for line in lines:
                if "# START BLOCKER MANAGED" in line:
                    in_managed_block = True
                    continue
                if "# END BLOCKER MANAGED" in line:
                    in_managed_block = False
                    continue
                
                if not in_managed_block:
                    new_lines.append(line)
            
            # Add our rules
            if self.rules["domain"]:
                new_lines.append("\n# START BLOCKER MANAGED\n")
                for domain in self.rules["domain"]:
                    new_lines.append(f"127.0.0.1 {domain}\n")
                    new_lines.append(f"127.0.0.1 www.{domain}\n")
                new_lines.append("# END BLOCKER MANAGED\n")
            
            with open(self.hosts_path, 'w') as f:
                f.writelines(new_lines)
                
        except PermissionError:
            print("Error: Permission denied writing to hosts file. Run as Admin.")
        except Exception as e:
            print(f"Error updating hosts file: {e}")


