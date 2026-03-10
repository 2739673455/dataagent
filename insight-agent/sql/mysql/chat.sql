SET GLOBAL time_zone = '+08:00';
SET SESSION time_zone = '+08:00';
DROP TABLE IF EXISTS `message`;
DROP TABLE IF EXISTS `conversation`;

CREATE TABLE `conversation` (
    `id` BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '对话ID',
    `user_id` BIGINT NOT NULL COMMENT '用户ID',
    `title` VARCHAR(200) NOT NULL COMMENT '对话标题',
    `create_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `update_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `yn` TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用',
    INDEX idx_conversation_user_id (user_id)
) COMMENT '对话';

CREATE TABLE `message` (
    `id` BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '消息ID',
    `conversation_id` BIGINT NOT NULL COMMENT '对话ID',
    `role` VARCHAR(20) NOT NULL COMMENT '角色 (user/assistant/tool/system)',
    `parts` TEXT NOT NULL COMMENT '消息片段列表 (JSON 字符串)',
    `attachments` TEXT NULL COMMENT '附件列表 (JSON 字符串)',
    `create_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `yn` TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用',
    FOREIGN KEY (`conversation_id`) REFERENCES `conversation` (`id`) ON DELETE CASCADE,
    INDEX idx_message_conversation_id (`conversation_id`)
) COMMENT '消息';
