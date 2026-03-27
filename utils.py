import os, sys, time

LOCK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db')

def acquire_lock(name, timeout=10):
    """Try to acquire a file lock. Returns True if acquired."""
    lock_path = os.path.join(LOCK_DIR, f'{name}.lock')
    try:
        # Check if stale lock (older than 2 hours)
        if os.path.exists(lock_path):
            age = time.time() - os.path.getmtime(lock_path)
            if age > 7200:  # 2 hours = stale
                os.remove(lock_path)
            else:
                return False
        with open(lock_path, 'w') as f:
            f.write(str(os.getpid()))
        return True
    except:
        return False

def release_lock(name):
    lock_path = os.path.join(LOCK_DIR, f'{name}.lock')
    try:
        os.remove(lock_path)
    except:
        pass
