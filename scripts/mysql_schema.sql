-- 南特金属智能客服 MySQL 5.7+ 表结构
-- 应用启动时会自动建库建表，本文件用于手工初始化或 DBA 审核。

CREATE DATABASE IF NOT EXISTS `nante`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `nante`;

CREATE TABLE IF NOT EXISTS `products` (
  `id` VARCHAR(32) NOT NULL COMMENT '商品编号',
  `name` VARCHAR(160) NOT NULL COMMENT '商品名称',
  `category` VARCHAR(64) DEFAULT '' COMMENT '品类',
  `system` VARCHAR(64) DEFAULT '' COMMENT '所属系统',
  `brand` VARCHAR(64) DEFAULT '' COMMENT '品牌',
  `price` DECIMAL(10,2) DEFAULT 0.00 COMMENT '价格',
  `price_text` VARCHAR(32) DEFAULT '' COMMENT '价格展示文本',
  `fitment` VARCHAR(255) DEFAULT '' COMMENT '适配车型',
  `stock` INT DEFAULT 0 COMMENT '库存',
  `warranty` VARCHAR(160) DEFAULT '' COMMENT '保修说明',
  `description` TEXT COMMENT '商品描述',
  `fields` JSON COMMENT '参数键值对',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_products_category` (`category`),
  KEY `idx_products_system` (`system`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `chat_sessions` (
  `id` VARCHAR(64) NOT NULL COMMENT '会话 ID',
  `title` VARCHAR(200) DEFAULT '' COMMENT '会话标题（首条用户消息）',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_sessions_updated` (`updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `chat_messages` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `session_id` VARCHAR(64) NOT NULL,
  `role` ENUM('user','assistant') NOT NULL,
  `content` TEXT NOT NULL,
  `intent` VARCHAR(32) DEFAULT '',
  `mode` VARCHAR(32) DEFAULT '',
  `latency_ms` INT DEFAULT 0,
  `cards` JSON COMMENT '商品卡片',
  `citations` JSON COMMENT '引用来源',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_messages_session` (`session_id`, `id`),
  CONSTRAINT `fk_messages_session` FOREIGN KEY (`session_id`)
    REFERENCES `chat_sessions` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `feedback` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `session_id` VARCHAR(64) DEFAULT '',
  `message_id` VARCHAR(64) DEFAULT '',
  `rating` ENUM('up','down') NOT NULL,
  `comment` VARCHAR(500) DEFAULT '',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_feedback_session` (`session_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
