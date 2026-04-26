import os
import time
import shutil
import schedule
from datetime import datetime

# ==============================================================================
# Automated Database & Memory Backup Manager
# Ensure your AI doesn't lose historical context if the VPS drops or docker fails.
# ==============================================================================

SOURCE_DIR = os.path.join(os.path.dirname(__file__), "dataflows", "persistent_memory")
BACKUP_DIR = os.path.join(os.path.dirname(__file__), "backups")

def perform_backup():
    print(f"[{datetime.now()}] Initiating Memory Database Backup...")
    
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        
    if not os.path.exists(SOURCE_DIR):
        print(f"[{datetime.now()}] Source directory {SOURCE_DIR} does not exist yet. Skipping.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(BACKUP_DIR, f"trading_memory_backup_{timestamp}")
    
    try:
        shutil.make_archive(backup_file, 'zip', SOURCE_DIR)
        print(f"[{datetime.now()}] ✅ Successfully backed up to {backup_file}.zip")
        
        # Retention Policy: Delete backups older than 7 days
        retention_days = 7
        now = time.time()
        for f in os.listdir(BACKUP_DIR):
            f_path = os.path.join(BACKUP_DIR, f)
            if os.stat(f_path).st_mtime < now - retention_days * 86400:
                os.remove(f_path)
                print(f"[{datetime.now()}] 🗑️ Deleted old backup: {f}")
                
    except Exception as e:
        print(f"[{datetime.now()}] ❌ Backup Failed: {e}")

if __name__ == "__main__":
    print(f"[{datetime.now()}] Backup Manager Started. Monitoring daily at 23:50.")
    # Run a backup right now on startup to secure baseline
    perform_backup()
    
    # Schedule daily backup just before midnight UTC/Local
    schedule.every().day.at("23:50").do(perform_backup)
    
    while True:
        schedule.run_pending()
        time.sleep(60)