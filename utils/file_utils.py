import os
import re
import tempfile
import string
import random

def safe_filename(filename):
    # Replace invalid characters with underscores
    safe_name = re.sub(r'[^\w\-\.]', '_', filename)
    
    # Ensure the filename isn't too long
    if len(safe_name) > 255:
        name, ext = os.path.splitext(safe_name)
        safe_name = name[:255-len(ext)] + ext
        
    return safe_name

def create_temp_directory(prefix="email_processor_"):
    return tempfile.mkdtemp(prefix=prefix)

def random_string(length=10):
    letters = string.ascii_lowercase + string.digits
    return ''.join(random.choice(letters) for _ in range(length))