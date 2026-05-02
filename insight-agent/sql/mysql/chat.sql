SET GLOBAL time_zone = '+08:00';
SET SESSION time_zone = '+08:00';
DROP TABLE IF EXISTS context_compaction;
DROP TABLE IF EXISTS message;
DROP TABLE IF EXISTS conversation;

CREATE TABLE conversation (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '对话ID',
    user_id BIGINT NOT NULL COMMENT '用户ID',
    title VARCHAR(128) NOT NULL COMMENT '对话标题',
    is_draft TINYINT NOT NULL DEFAULT 0 COMMENT '是否为草稿对话',
    create_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用',
    INDEX idx_conversation_user_id (user_id)
) COMMENT '对话';

CREATE TABLE message (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '消息ID',
    conversation_id BIGINT NOT NULL COMMENT '对话ID',
    context_seq BIGINT NOT NULL COMMENT '对话内上下文顺序号(从0起)',
    role VARCHAR(10) NOT NULL COMMENT '角色 (user/assistant/tool/system)',
    parts MEDIUMTEXT NOT NULL COMMENT '消息片段列表 (JSON 字符串)',
    finish_reason VARCHAR(128) NULL COMMENT '完成原因',
    attachments TEXT NULL COMMENT '附件列表 (JSON 字符串)',
    create_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用',
    FOREIGN KEY (conversation_id) REFERENCES conversation (id) ON DELETE CASCADE,
    INDEX idx_message_conversation_id (conversation_id),
    UNIQUE KEY uk_message_conversation_id_context_seq (conversation_id, context_seq)
) COMMENT '消息';

CREATE TABLE context_compaction (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '上下文压缩ID',
    conversation_id BIGINT NOT NULL COMMENT '对话ID',
    end_seq BIGINT NOT NULL COMMENT '本次压缩覆盖的结束上下文顺序号(0-based, 不包含)',
    summary_message MEDIUMTEXT NOT NULL COMMENT '压缩后的摘要内容',
    create_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用',
    FOREIGN KEY (conversation_id) REFERENCES conversation (id) ON DELETE CASCADE,
    INDEX idx_context_compaction_conversation_id (conversation_id),
    INDEX idx_context_compaction_end_seq (conversation_id, end_seq)
) COMMENT '上下文压缩事件';
