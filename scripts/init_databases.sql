-- Initialize 6 separate logical databases for DEC-002 service isolation
SELECT 'CREATE DATABASE forum_users'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'forum_users')\gexec

SELECT 'CREATE DATABASE forum_content'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'forum_content')\gexec

SELECT 'CREATE DATABASE forum_interactions'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'forum_interactions')\gexec

SELECT 'CREATE DATABASE forum_moderation'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'forum_moderation')\gexec

SELECT 'CREATE DATABASE forum_notifications'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'forum_notifications')\gexec

SELECT 'CREATE DATABASE forum_search'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'forum_search')\gexec
