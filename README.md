<!--
  PROJECT README — Construction E-Invoice System
  Put this file as README.md in the root of that repo.
  Replace HussainAliAbdulAzizAli and screenshot placeholders once the repo is live.
-->

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=1F4E79&height=180&section=header&text=Construction%20E-Invoice%20System&fontSize=32&fontColor=ffffff&animation=fadeIn" />
</p>

<p align="center">
  <img src="https://readme-typing-svg.demolab.com?font=Fira+Code&size=18&pause=1200&color=1F4E79&center=true&vCenter=true&width=560&lines=Full-stack+e-invoicing+platform+for+construction;Flask+%2B+MySQL+%2B+OCR+%2B+Automated+PDF+Generation;Senior+Graduation+Project+-+University+of+Bahrain" alt="Typing SVG" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Flask-000000?style=for-the-badge&logo=flask&logoColor=white" />
  <img src="https://img.shields.io/badge/MySQL-4479A1?style=for-the-badge&logo=mysql&logoColor=white" />
  <img src="https://img.shields.io/badge/OpenCV-5C3EE8?style=for-the-badge&logo=opencv&logoColor=white" />
  <img src="https://img.shields.io/badge/status-completed-brightgreen?style=for-the-badge" />
</p>

---

## 📖 Overview

A full-stack web application that digitizes invoicing for construction projects — from invoice creation and OCR-based scanning, through automated PDF generation, email payment workflows, and real-time financial reporting.

## ✨ Key Features

- 🔐 **Authentication** — secure signup/login with password reset via email
- 🧾 **Invoice Management** — create, edit, track, and delete invoices with full status history
- 🔍 **OCR Invoice Scanning** — automatically extracts vendor, date, and totals from uploaded invoice images (OpenCV)
- 💳 **Payment Workflow** — secure customer payment links, confirmation emails, and automatic overdue tracking
- 📄 **PDF Generation** — one-click professional invoice PDFs
- 📊 **Financial Dashboards** — live balance sheet, profit & loss, cash flow, and invoice analytics

## 🛠️ Tech Stack

**Backend:** Python, Flask, MySQL, OpenCV
**Frontend:** HTML5, CSS3, JavaScript
**Other:** ReportLab (PDF generation), Flask-CORS, SMTP email integration

## 📸 Screenshots

| Home / Landing Page | Invoice Upload |
|---|---|
| ![Home](./screenshots/home-landing.png) | ![Invoice Upload](./screenshots/invoice-upload.png) |

## 🚀 Getting Started

```bash
# Clone the repo
git clone https://github.com/HussainAliAbdulAzizAli/construction-einvoice.git
cd construction-einvoice/backend

# Install dependencies
pip install -r requirements.txt

# Set up your MySQL database
mysql -u root -p < ../DataBase.sql

# Configure environment variables (see .env.example)
cp .env.example .env

# Run the app
python app.py
```

## 🗂️ Project Structure

```
construction-einvoice/
├── backend/          # Flask app, OCR processor, API routes
├── frontend/         # HTML/CSS/JS pages
└── DataBase.sql       # MySQL schema
```

## 👥 Team

This was a team senior graduation project — University of Bahrain, College of Information Technology.

| Name | Role |
|---|---|
| **Hussain Ali A. Aziz Ali** | Full-stack development — [GitHub](https://github.com/HussainAliAbdulAzizAli) |
| **Abdulaziz Mohammad** | Backend & data automation (OCR invoice import) |
| **Arfaj Alkaabi** | Front-end development & UI |

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=1F4E79&height=100&section=footer" />
</p>
