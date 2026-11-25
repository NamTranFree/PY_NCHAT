CREATE DATABASE IF NOT EXISTS python_chat
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE python_chat;

CREATE TABLE IF NOT EXISTS users (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    birth_date DATE NULL,
    interests VARCHAR(500) NOT NULL DEFAULT '',
    hometown VARCHAR(100) NOT NULL DEFAULT '',
    is_display_name_public TINYINT(1) NOT NULL DEFAULT 1,
    is_birth_date_public TINYINT(1) NOT NULL DEFAULT 0,
    is_interests_public TINYINT(1) NOT NULL DEFAULT 0,
    is_hometown_public TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS messages (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    sender_id BIGINT UNSIGNED NOT NULL,
    recipient_id BIGINT UNSIGNED NOT NULL,
    body TEXT NOT NULL,
    attachment_name VARCHAR(255) NULL,
    attachment_type VARCHAR(100) NULL,
    attachment_path VARCHAR(500) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_messages_sender FOREIGN KEY (sender_id) REFERENCES users(id),
    CONSTRAINT fk_messages_recipient FOREIGN KEY (recipient_id) REFERENCES users(id),
    INDEX idx_conversation (sender_id, recipient_id, id),
    INDEX idx_conversation_reverse (recipient_id, sender_id, id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS chat_groups (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(80) NOT NULL,
    created_by BIGINT UNSIGNED NOT NULL,
    leader_id BIGINT UNSIGNED NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_chat_groups_creator FOREIGN KEY (created_by) REFERENCES users(id),
    CONSTRAINT fk_chat_groups_leader FOREIGN KEY (leader_id) REFERENCES users(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS group_members (
    group_id BIGINT UNSIGNED NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,
    joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (group_id, user_id),
    CONSTRAINT fk_group_members_group FOREIGN KEY (group_id) REFERENCES chat_groups(id) ON DELETE CASCADE,
    CONSTRAINT fk_group_members_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_group_members_user (user_id, group_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS group_messages (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    group_id BIGINT UNSIGNED NOT NULL,
    sender_id BIGINT UNSIGNED NOT NULL,
    body TEXT NOT NULL,
    message_type VARCHAR(20) NOT NULL DEFAULT 'message',
    attachment_name VARCHAR(255) NULL,
    attachment_type VARCHAR(100) NULL,
    attachment_path VARCHAR(500) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_group_messages_group FOREIGN KEY (group_id) REFERENCES chat_groups(id) ON DELETE CASCADE,
    CONSTRAINT fk_group_messages_sender FOREIGN KEY (sender_id) REFERENCES users(id),
    INDEX idx_group_messages_history (group_id, id)
) ENGINE=InnoDB;
