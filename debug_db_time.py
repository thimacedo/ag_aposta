import time
import db
from pathlib import Path

def test_init_time():
    print("Measuring db.init_db() execution time...")
    start = time.time()
    db.init_db()
    end = time.time()
    print(f"db.init_db() took {end - start:.4f} seconds.")

if __name__ == "__main__":
    test_init_time()
