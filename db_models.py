# db_models.py
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(200))
    role = db.Column(db.String(20))
    gate_assigned = db.Column(db.String(10), nullable=True)
    logs = db.relationship('GuardLog', backref='user', lazy=True)

class Person(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    id_number = db.Column(db.String(20), unique=True)
    name = db.Column(db.String(100))
    role = db.Column(db.String(20))  # e.g., Estudiante, Profesor
    picture = db.Column(db.String(200))
    plate = db.Column(db.String(20), unique=True, nullable=True)
    flag = db.Column(db.String(20), nullable=True)  # e.g., 'Infracción emitida'
    access_logs = db.relationship('AccessLog', backref='person', lazy=True)

class AccessLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey('person.id'), nullable=True)
    plate = db.Column(db.String(20))
    action = db.Column(db.String(20))  # 'allowed' o 'denied'
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    guard_id = db.Column(db.Integer, db.ForeignKey('user.id'))

class GuardLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guard_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    gate = db.Column(db.String(10))
    action = db.Column(db.String(20))  # e.g., 'sign_in', 'sign_out'
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
