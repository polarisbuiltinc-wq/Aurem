"""Seed a github_installations row for the test user. Usage: python seed_installs.py <user_id> <num_repos>"""
import sys, time
from pymongo import MongoClient

user_id = sys.argv[1]
num_repos = int(sys.argv[2]) if len(sys.argv) > 2 else 1

client = MongoClient("mongodb://localhost:27017")
db = client["aurem_dev"]

# Delete existing installations for this user
db.github_installations.delete_many({"user_id": user_id})

repos_single = [{"id": 1, "full_name": "test-owner/single-repo", "default_branch": "main", "private": False}]
repos_multi = [
    {"id": 1, "full_name": "test-owner/repo-alpha", "default_branch": "main", "private": False},
    {"id": 2, "full_name": "test-owner/repo-beta",  "default_branch": "develop", "private": True},
    {"id": 3, "full_name": "test-owner/repo-gamma", "default_branch": "main", "private": False},
]
repos = repos_single if num_repos == 1 else repos_multi

doc = {
    "user_id": user_id,
    "installation_id": 999001 if num_repos == 1 else 999002,
    "github_login": "test-owner",
    "repositories": repos,
    "active": True,
    "installed_at": time.time(),
}
db.github_installations.insert_one(doc)
print(f"Seeded install with {len(repos)} repos for user_id={user_id}")
print("Repos:", [r["full_name"] for r in repos])
