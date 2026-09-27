CREATE DATABASE IF NOT EXISTS invoice_system;
USE invoice_system;

CREATE TABLE IF NOT EXISTS users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(100),
    role ENUM('admin', 'user') DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP NULL,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS invoices (
    id VARCHAR(36) PRIMARY KEY,
    invoice_number VARCHAR(50),
    date DATE,
    due_date DATE,
    vendor_name VARCHAR(200),
    vendor_address TEXT,
    customer_name VARCHAR(200),
    customer_address TEXT,
    subtotal DECIMAL(12,2),
    tax DECIMAL(12,2),
    total DECIMAL(12,2),
    currency VARCHAR(3) DEFAULT 'USD',
    payment_terms VARCHAR(100),
    notes TEXT,
    file_path VARCHAR(500),
    file_name VARCHAR(200),
    file_size INT,
    status ENUM('DRAFT', 'PENDING', 'PAID', 'OVERDUE', 'CANCELLED') DEFAULT 'DRAFT',
    payment_link VARCHAR(500),
    payment_link_expires DATETIME,
    payment_received_date DATETIME,
    payment_method VARCHAR(50),
    payment_transaction_id VARCHAR(100),
    email_sent_at DATETIME,
    email_sent_to VARCHAR(100),
    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uploaded_by INT,
    last_modified TIMESTAMP NULL,
    modified_by INT,
    FOREIGN KEY (uploaded_by) REFERENCES users(id),
    FOREIGN KEY (modified_by) REFERENCES users(id),
    INDEX idx_status (status),
    INDEX idx_invoice_number (invoice_number),
    INDEX idx_due_date (due_date)
);

CREATE TABLE IF NOT EXISTS construction_details (
    id INT PRIMARY KEY AUTO_INCREMENT,
    invoice_id VARCHAR(36),
    project_name VARCHAR(200),
    project_address TEXT,
    project_number VARCHAR(50),
    contractor_license VARCHAR(50),
    work_order VARCHAR(50),
    material_cost DECIMAL(12,2),
    labor_cost DECIMAL(12,2),
    equipment_cost DECIMAL(12,2),
    permit_number VARCHAR(50),
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS invoice_items (
    id INT PRIMARY KEY AUTO_INCREMENT,
    invoice_id VARCHAR(36),
    item_number INT,
    description TEXT,
    quantity VARCHAR(50),
    unit_price DECIMAL(12,2),
    total DECIMAL(12,2),
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS invoice_logs (
    id INT PRIMARY KEY AUTO_INCREMENT,
    invoice_id VARCHAR(36),
    action ENUM('UPLOAD', 'VIEW', 'EDIT', 'DELETE', 'DOWNLOAD', 'EXPORT', 'SEND_TO_CUSTOMER', 'STATUS_PAID', 'STATUS_PENDING', 'STATUS_OVERDUE', 'STATUS_CANCELLED') NOT NULL,
    user_id INT,
    username VARCHAR(50),
    user_email VARCHAR(100),
    action_details TEXT,
    ip_address VARCHAR(45),
    user_agent TEXT,
    changes JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_invoice_id (invoice_id),
    INDEX idx_user_id (user_id),
    INDEX idx_action (action),
    INDEX idx_created_at (created_at),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id VARCHAR(128) PRIMARY KEY,
    user_id INT,
    ip_address VARCHAR(45),
    user_agent TEXT,
    payload TEXT,
    last_activity INT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS password_reset_codes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    code CHAR(6) NOT NULL,
    expires_at DATETIME NOT NULL,
    used TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_email (email),
    INDEX idx_expires (expires_at)
);

CREATE TABLE IF NOT EXISTS payment_tokens (
    id INT PRIMARY KEY AUTO_INCREMENT,
    invoice_id VARCHAR(36) NOT NULL,
    token VARCHAR(255) NOT NULL UNIQUE,
    expires_at DATETIME NOT NULL,
    used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
    INDEX idx_token (token),
    INDEX idx_invoice_id (invoice_id)
);

CREATE TABLE IF NOT EXISTS payment_status_log (
    id INT PRIMARY KEY AUTO_INCREMENT,
    invoice_id VARCHAR(36) NOT NULL,
    old_status VARCHAR(20),
    new_status VARCHAR(20) NOT NULL,
    changed_by INT,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
    FOREIGN KEY (changed_by) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_invoice_id (invoice_id)
);

CREATE TABLE IF NOT EXISTS payment_notifications (
    id INT PRIMARY KEY AUTO_INCREMENT,
    invoice_id VARCHAR(36) NOT NULL,
    notification_type VARCHAR(50) NOT NULL,
    sent_to VARCHAR(255),
    status VARCHAR(20),
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
    INDEX idx_invoice_id (invoice_id)
);

CREATE TABLE IF NOT EXISTS user_settings (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL UNIQUE,
    default_currency VARCHAR(3) DEFAULT 'USD',
    email_notifications BOOLEAN DEFAULT TRUE,
    payment_reminders BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS contact_messages (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    subject VARCHAR(500) NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_read BOOLEAN DEFAULT FALSE
);