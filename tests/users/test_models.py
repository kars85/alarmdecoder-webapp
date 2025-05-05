# tests/users/test_models.py
from ad2web.user.models import User

def test_password_hashing_and_verification():
    # Create a user and verify password hashing
    u = User(name="alice", email="alice@example.com", password="mysecret", role_code=1, status_code=2)
    # The password should be hashed (not equal to plaintext)&#8203;:contentReference[oaicite:15]{index=15}
    assert u.password != "mysecret"
    # Correct password should verify True, wrong password False
    assert u.check_password("mysecret") is True
    assert u.check_password("wrongpass") is False

def test_follow_unfollow_relationship(db_session):
    # Create two users
    u1 = User(name="user1", email="user1@example.com", password="pass1", role_code=1, status_code=2)
    u2 = User(name="user2", email="user2@example.com", password="pass2", role_code=1, status_code=2)
    db_session.session.add_all([u1, u2])
    db_session.session.commit()
    # Initially, no followers or following for each user
    assert u1.num_followers == 0 and u1.num_following == 0
    assert u2.num_followers == 0 and u2.num_following == 0
    # user1 follows user2
    u1.follow(u2)
    # Now user1 is following user2, and user2 has user1 as follower&#8203;:contentReference[oaicite:16]{index=16}
    assert u1.num_following == 1 and u2.num_followers == 1
    # The follow query helpers should return the correct user
    assert u1.get_following_query().first().id == u2.id
    assert u2.get_followers_query().first().id == u1.id
    # user1 unfollows user2
    u1.unfollow(u2)
    # Relationship counts should decrement back to zero
    assert u1.num_following == 0 and u2.num_followers == 0
