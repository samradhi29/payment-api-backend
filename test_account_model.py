from sqlalchemy import select, func

from app.core.database import SessionLocal
from app.models.account import Account


def test_account_model():
    with SessionLocal() as db:
        count = db.scalar(
            select(func.count()).select_from(Account)
        )

        print("Account model connected successfully!")
        print("Existing accounts:", count)


if __name__ == "__main__":
    test_account_model()