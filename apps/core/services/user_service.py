from helpers.database import get_platform_db


def get_user_by_email(email):
    return get_platform_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def get_user_by_id(user_id):
    return get_platform_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_user(name, email, password_hash, role="platform_admin"):
    db = get_platform_db()
    cur = db.execute(
        "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
        (name, email, password_hash, role),
    )
    db.commit()
    return cur.lastrowid
