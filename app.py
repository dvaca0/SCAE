# app.py
import logging
import os
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from dotenv import load_dotenv
from db_models import db, User, Person, AccessLog, GuardLog
from detection import detectar_vehiculos
from lpr import reconocer_placa
from notifications import enviar_notificacion
import cv2
import numpy as np
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

load_dotenv()
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY is not set. Copy .env.example to .env and fill it in.")
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    "DATABASE_URL", "sqlite:///vehicular_access_control.db"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

def populate_db():
    with app.app_context():
        # Verifica si los datos ya existen
        if User.query.first():
            logging.debug("Usuarios ya existen en la base de datos.")
            return

        logging.debug("Poblando la base de datos con usuarios iniciales.")

        # Crea administrador
        admin = User(
            name='Admin',
            username='admin',
            password=generate_password_hash('admin123'),
            role='admin'
        )
        db.session.add(admin)

        # Crea guardias (demo credentials — change before any real deployment)
        guards = [
            User(name='Alex Guard', username='guard1', password=generate_password_hash('pass1'), role='guard', gate_assigned='A1'),
            User(name='Sam Guard', username='guard2', password=generate_password_hash('pass2'), role='guard', gate_assigned='A2'),
        ]
        db.session.add_all(guards)

        # Agrega personas de ejemplo a la base de datos (datos ficticios para la demo)
        people = [
            Person(id_number='S100001', name='Jordan Rivera', role='Estudiante', picture='placeholder.jpg', plate='DEMO001'),
            Person(id_number='S100002', name='Taylor Morgan', role='Estudiante', picture='placeholder.jpg', plate='DEMO002'),
            Person(id_number='P100003', name='Casey Nguyen', role='Profesor', picture='placeholder.jpg', plate='DEMO003'),
            # Datos de ejemplo — reemplaza con tus propios registros en un despliegue real
        ]
        db.session.add_all(people)
        db.session.commit()  # Importante: Confirmar antes de usar los IDs

        # Crear registros de accesos específicos
        from datetime import datetime, timedelta, timezone

        # Actualiza los IDs de las personas y guardias después de commit
        guards = User.query.filter_by(role='guard').all()
        people = Person.query.all()

        # Registros de acceso
        access_logs = [
            AccessLog(
                person_id=people[0].id,
                plate=people[0].plate,
                action='allowed',
                timestamp=datetime.now(timezone.utc) - timedelta(hours=2),
                guard_id=guards[0].id
            ),
            AccessLog(
                person_id=people[1].id,
                plate=people[1].plate,
                action='allowed',
                timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
                guard_id=guards[0].id
            ),
        ]
        db.session.add_all(access_logs)

        # Crear logs de guardias
        guard_logs = [
            GuardLog(
                guard_id=guards[0].id,
                gate=guards[0].gate_assigned,
                action='sign_in',
                timestamp=datetime.now(timezone.utc) - timedelta(hours=8)
            ),
            GuardLog(
                guard_id=guards[0].id,
                gate=guards[0].gate_assigned,
                action='sign_out',
                timestamp=datetime.now(timezone.utc)
            ),
        ]
        db.session.add_all(guard_logs)

        db.session.commit()
        logging.debug("Base de datos poblada exitosamente con datos específicos.")

# Funciones auxiliares de autenticación y autorización
def login_user(user):
    session['user_id'] = user.id

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def roles_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = User.query.get(session.get('user_id'))
            if not user or user.role not in roles:
                return "No autorizado", 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Rutas
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.is_json:
            # La solicitud es JSON
            data = request.get_json()
            username = data.get('username')
            password = data.get('password')
        else:
            # La solicitud es Form Data
            username = request.form.get('username')
            password = request.form.get('password')
        logging.debug(f"Intento de inicio de sesión con usuario: {username}")
        user = User.query.filter_by(username=username).first()
        if user:
            logging.debug(f"Usuario encontrado: {user.username}")
            if check_password_hash(user.password, password):
                logging.debug("Contraseña verificada correctamente")
                login_user(user)
                if request.is_json:
                    return jsonify({'status': 'success', 'role': user.role}), 200
                else:
                    if user.role == 'admin':
                        return redirect(url_for('admin_dashboard'))
                    elif user.role == 'guard':
                        # Registrar inicio de sesión del guardia
                        log = GuardLog(guard_id=user.id, gate=user.gate_assigned, action='sign_in')
                        db.session.add(log)
                        db.session.commit()
                        return redirect(url_for('guard_dashboard'))
                    else:
                        flash('Rol de usuario no reconocido.')
                        return redirect(url_for('login'))
            else:
                logging.debug("Contraseña incorrecta")
                if request.is_json:
                    return jsonify({'status': 'error', 'message': 'Contraseña incorrecta.'}), 401
                else:
                    flash('Usuario o contraseña inválidos.')
        else:
            logging.debug("Usuario no encontrado")
            if request.is_json:
                return jsonify({'status': 'error', 'message': 'Usuario no encontrado.'}), 404
            else:
                flash('Usuario o contraseña inválidos.')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    user = User.query.get(session['user_id'])
    if user.role == 'guard':
        # Registra la salida del guardia
        log = GuardLog(guard_id=user.id, gate=user.gate_assigned, action='sign_out')
        db.session.add(log)
        db.session.commit()
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    user = User.query.get(session['user_id'])
    if user.role == 'guard':
        return redirect(url_for('guard_dashboard'))
    elif user.role == 'admin':
        return redirect(url_for('admin_dashboard'))

# Rutas de administrador
@app.route('/admin')
@login_required
@roles_required('admin')
def admin_dashboard():
    return render_template('admin_dashboard.html')

@app.route('/admin/guards', methods=['GET', 'POST'])
@login_required
@roles_required('admin')
def manage_guards():
    if request.method == 'POST':
        action = request.form.get('action') or request.json.get('action')
        if action == 'add':
            name = request.form.get('name') or request.json.get('name')
            username = request.form.get('username') or request.json.get('username')
            password = request.form.get('password') or request.json.get('password')
            gate_assigned = request.form.get('gate_assigned') or request.json.get('gate_assigned')
            new_guard = User(
                name=name,
                username=username,
                password=generate_password_hash(password),
                role='guard',
                gate_assigned=gate_assigned
            )
            db.session.add(new_guard)
            db.session.commit()
            if request.is_json:
                return jsonify({'status': 'success', 'message': 'Guardia agregado.'})
        elif action == 'remove':
            guard_id = request.form.get('guard_id') or request.json.get('guard_id')
            guard = User.query.get(guard_id)
            db.session.delete(guard)
            db.session.commit()
            if request.is_json:
                return jsonify({'status': 'success', 'message': 'Guardia eliminado.'})
        elif action == 'update':
            guard_id = request.form.get('guard_id') or request.json.get('guard_id')
            gate_assigned = request.form.get('gate_assigned') or request.json.get('gate_assigned')
            guard = User.query.get(guard_id)
            guard.gate_assigned = gate_assigned
            db.session.commit()
            if request.is_json:
                return jsonify({'status': 'success', 'message': 'Puerta asignada actualizada.'})
    guards = User.query.filter_by(role='guard').all()
    return render_template('manage_guards.html', guards=guards)

# Rutas API para obtener registros de acceso y logs de guardias
@app.route('/api/admin/access_logs', methods=['GET'])
@login_required
@roles_required('admin')
def api_get_access_logs():
    access_logs = AccessLog.query.order_by(AccessLog.timestamp.desc()).all()
    logs_list = []
    for log in access_logs:
        person = Person.query.get(log.person_id)
        guard = User.query.get(log.guard_id)
        logs_list.append({
            'id': log.id,
            'person_name': person.name if person else 'Desconocido',
            'person_id_number': person.id_number if person else 'Desconocido',
            'plate': log.plate,
            'action': log.action,
            'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'guard_name': guard.name if guard else 'Desconocido',
        })
    return jsonify({'access_logs': logs_list})

@app.route('/api/admin/guard_logs', methods=['GET'])
@login_required
@roles_required('admin')
def api_get_guard_logs():
    guard_logs = GuardLog.query.order_by(GuardLog.timestamp.desc()).all()
    logs_list = []
    for log in guard_logs:
        guard = User.query.get(log.guard_id)
        logs_list.append({
            'id': log.id,
            'guard_name': guard.name if guard else 'Desconocido',
            'gate': log.gate,
            'action': log.action,
            'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
        })
    return jsonify({'guard_logs': logs_list})

# Nueva ruta API para obtener la lista de guardias
@app.route('/api/admin/guards', methods=['GET'])
@login_required
@roles_required('admin')
def api_get_guards():
    user = User.query.get(session.get('user_id'))
    logging.debug(f"Usuario autenticado: {user.username} con rol {user.role}")
    guards = User.query.filter_by(role='guard').all()
    guards_list = [{
        'id': guard.id,
        'name': guard.name,
        'username': guard.username,
        'gate_assigned': guard.gate_assigned
    } for guard in guards]
    return jsonify({'guards': guards_list})

# Rutas de guardia
@app.route('/guard')
@login_required
@roles_required('guard')
def guard_dashboard():
    return render_template('guard_dashboard.html')

@app.route('/guard/process_image', methods=['POST'])
@login_required
@roles_required('guard')
def process_image():
    try:
        file = request.files['image']
        npimg = np.frombuffer(file.read(), np.uint8)
        frame = cv2.imdecode(npimg, cv2.IMREAD_COLOR)

        vehiculos_detectados = detectar_vehiculos(frame)
        response_data = []

        # Si no se detectan vehículos, responder con 'no_plate'
        if vehiculos_detectados.empty:
            response_data.append({'status': 'no_plate'})
            return jsonify(response_data)

        for index, vehiculo in vehiculos_detectados.iterrows():
            x_min, y_min, x_max, y_max = int(vehiculo['xmin']), int(vehiculo['ymin']), int(vehiculo['xmax']), int(vehiculo['ymax'])
            imagen_vehiculo = frame[y_min:y_max, x_min:x_max]
            placa = reconocer_placa(imagen_vehiculo)
            if placa:
                person = Person.query.filter_by(plate=placa).first()
                if person:
                    # Placa reconocida y persona existe
                    access_log = AccessLog(person_id=person.id, plate=placa, action='allowed', guard_id=session['user_id'])
                    db.session.add(access_log)
                    db.session.commit()
                    # Enviar datos al frontend
                    response_data.append({
                        'status': 'recognized',
                        'person': {
                            'id_number': person.id_number,
                            'name': person.name,
                            'role': person.role,
                            'picture': person.picture,
                            'flag': person.flag,
                            'id': person.id
                        }
                    })
                else:
                    # Placa reconocida pero no asociada a una persona
                    response_data.append({'status': 'unrecognized_plate', 'plate': placa})
            else:
                # No se reconoció ninguna placa
                response_data.append({'status': 'no_plate'})

        if not response_data:
            response_data.append({'status': 'no_plate'})

        return jsonify(response_data)
    except Exception as e:
        logging.exception("Error al procesar la imagen")
        return jsonify({'status': 'error', 'message': 'Error al procesar la imagen.'}), 500

@app.route('/guard/assign_plate', methods=['POST'])
@login_required
@roles_required('guard')
def assign_plate():
    data = request.get_json()
    id_number = data['id_number']
    plate = data['plate']

    person = Person.query.filter_by(id_number=id_number).first()
    if person:
        person.plate = plate
        db.session.commit()
        return jsonify({'status': 'success', 'message': 'Placa asignada exitosamente.'})
    else:
        return jsonify({'status': 'error', 'message': 'Persona no encontrada.'}), 404

@app.route('/guard/issue_ticket', methods=['POST'])
@login_required
@roles_required('guard')
def issue_ticket():
    data = request.get_json()
    person_id = data['person_id']
    reason = data['reason']

    person = Person.query.get(person_id)
    if person:
        person.flag = 'Infracción emitida'
        db.session.commit()
        # Enviar notificación por correo electrónico
        enviar_notificacion(
            to_email='person@example.com',  # Reemplaza con el correo electrónico de la persona
            subject='Ha recibido una infracción',
            message=f'Estimado/a {person.name},\n\nHa recibido una infracción por la siguiente razón:\n{reason}\n\nPor favor, comuníquese con seguridad para más información.'
        )
        return jsonify({'status': 'success', 'message': 'Infracción emitida.'})
    else:
        return jsonify({'status': 'error', 'message': 'Persona no encontrada.'}), 404

@app.route('/guard/deny_access', methods=['POST'])
@login_required
@roles_required('guard')
def deny_access():
    data = request.get_json()
    person_id = data.get('person_id')
    plate = data.get('plate')

    access_log = AccessLog(
        person_id=person_id,
        plate=plate,
        action='denied',
        guard_id=session['user_id']
    )
    db.session.add(access_log)
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Acceso denegado.'})

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        populate_db()
    app.run(debug=True)
