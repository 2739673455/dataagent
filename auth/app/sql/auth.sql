SET GLOBAL time_zone = '+08:00';
SET SESSION time_zone = '+08:00';
DROP TABLE IF EXISTS `refresh_token`;
DROP TABLE IF EXISTS `group_user_rel`;
DROP TABLE IF EXISTS `group_scope_rel`;
DROP TABLE IF EXISTS `user`;
DROP TABLE IF EXISTS `group`;
DROP TABLE IF EXISTS `scope`;

CREATE TABLE `scope` (
    `id` INT AUTO_INCREMENT COMMENT '权限范围ID',
    `name` VARCHAR(100) NOT NULL COMMENT '权限范围名称',
    `description` VARCHAR(100) DEFAULT NULL COMMENT '权限范围描述',
    `create_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `yn` TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用',
    PRIMARY KEY (`id`)
) COMMENT '权限范围';

CREATE TABLE `group` (
    `id` INT AUTO_INCREMENT COMMENT '组ID',
    `name` VARCHAR(100) NOT NULL COMMENT '组名称',
    `create_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `yn` TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用',
    PRIMARY KEY (`id`)
) COMMENT '组';

CREATE TABLE `user` (
    `id` BIGINT AUTO_INCREMENT COMMENT '用户ID',
    `email` VARCHAR(100) UNIQUE NOT NULL COMMENT '邮箱',
    `name` VARCHAR(100) NOT NULL COMMENT '用户名',
    `password_hash` VARCHAR(500) NOT NULL COMMENT '密码哈希',
    `create_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `update_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `yn` TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用',
    PRIMARY KEY (`id`)
) COMMENT '用户';

CREATE TABLE `group_scope_rel` (
    `group_id` INT NOT NULL COMMENT '组ID',
    `scope_id` INT NOT NULL COMMENT '权限范围ID',
    PRIMARY KEY (`group_id`, `scope_id`),
    FOREIGN KEY (`group_id`) REFERENCES `group` (`id`),
    FOREIGN KEY (`scope_id`) REFERENCES `scope` (`id`)
) COMMENT '组-权限关系';

CREATE TABLE `group_user_rel` (
    `group_id` INT NOT NULL COMMENT '组ID',
    `user_id` BIGINT NOT NULL COMMENT '用户ID',
    PRIMARY KEY (`group_id`, `user_id`),
    FOREIGN KEY (`group_id`) REFERENCES `group` (`id`),
    FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
) COMMENT '组-用户关系';

CREATE TABLE `refresh_token` (
    `jti` VARCHAR(255) NOT NULL COMMENT 'JWT唯一标识',
    `user_id` BIGINT NOT NULL COMMENT '用户ID',
    `create_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `expires_at` DATETIME NOT NULL COMMENT '过期时间',
    `yn` TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用',
    PRIMARY KEY (`jti`),
    FOREIGN KEY (`user_id`) REFERENCES `user` (`id`) ON DELETE CASCADE,
    INDEX idx_refresh_token_user_id (user_id)
) COMMENT '刷新令牌';

-- 初始化数据
INSERT INTO `group` (`name`) VALUES ('normal');
