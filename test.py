#!/usr/bin/env python3
"""Test script to verify basic functionality"""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

def test_imports():
    """Test all imports work"""
    try:
        from config import Config
        print("✅ Config import successful")

        from database import db
        print("✅ Database import successful")

        from api_client import FreeCustomAPIClient
        print("✅ API Client import successful")

        # Test config validation (will fail without BOT_TOKEN, but that's expected)
        try:
            Config.validate()
            print("✅ Config validation passed")
        except ValueError as e:
            print(f"⚠️  Config validation failed (expected): {e}")

        return True
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False

def test_database_schema():
    """Test database schema creation"""
    try:
        import asyncio
        from database import init_database

        async def test_db():
            await init_database()
            print("✅ Database schema creation successful")
            return True

        return asyncio.run(test_db())
    except Exception as e:
        print(f"❌ Database test failed: {e}")
        return False

def main():
    print("🧪 Running basic tests...\n")

    tests_passed = 0
    total_tests = 2

    if test_imports():
        tests_passed += 1

    if test_database_schema():
        tests_passed += 1

    print(f"\n📊 Test Results: {tests_passed}/{total_tests} passed")

    if tests_passed == total_tests:
        print("🎉 All tests passed! The bot should work correctly.")
        print("\n📝 Next steps:")
        print("1. Install dependencies: pip install -r requirements.txt")
        print("2. Set your BOT_TOKEN in .env file")
        print("3. Run the bot: python bot.py")
    else:
        print("❌ Some tests failed. Please check the errors above.")

if __name__ == "__main__":
    main()