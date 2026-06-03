#!/usr/bin/env python3
"""Create or reset an admin user. Run after init_db.py."""
import sys
import os
import getpass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from models import User

app = create_app()

with app.app_context():
    username = input("Admin username: ").strip()
    if not username:
        print("Username required.")
        sys.exit(1)

    password = getpass.getpass("Password: ")
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        sys.exit(1)

    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.")
        sys.exit(1)

    user = User.query.filter_by(username=username).first()
    if user:
        user.set_password(password)
        user.role = "admin"
        user.active = True
        db.session.commit()
        print(f"Admin '{username}' updated.")
    else:
        user = User(username=username, role="admin", active=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Admin '{username}' created.")
