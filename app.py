from flask import Flask, render_template, request, jsonify, redirect, url_for
import stripe
import sqlite3
from decouple import config
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = config('SECRET_KEY')

# Stripe API Configuration
stripe.api_key = config('STRIPE_SECRET_KEY')
STRIPE_PUBLISHABLE_KEY = config('STRIPE_PUBLISHABLE_KEY')
STRIPE_WEBHOOK_SECRET = config('STRIPE_WEBHOOK_SECRET')

DATABASE_URL = config('DATABASE_URL').replace('sqlite:///', '')

@app.route('/')
d...
}
