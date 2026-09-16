from .db import migrate

if __name__ == "__main__":
    applied = migrate()
    if applied:
        print("applied migrations:", ", ".join(applied))
    else:
        print("migrations up to date")
