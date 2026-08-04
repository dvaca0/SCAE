# dashboard/dashboard.py
import streamlit as st
import requests
from PIL import Image
import os

def login():
    st.title("🔐 Inicio de Sesión")
    username = st.text_input("Usuario")
    password = st.text_input("Contraseña", type="password")
    if st.button("Iniciar Sesión"):
        session = requests.Session()
        response = session.post(
            'http://localhost:5000/login',
            json={'username': username, 'password': password}
        )
        if response.ok:
            data = response.json()
            st.session_state['logged_in'] = True
            st.session_state['username'] = username
            st.session_state['role'] = data['role']
            st.session_state['session'] = session  # Guardar la sesión
            st.success(f"Sesión iniciada exitosamente como {data['role']}")
            st.experimental_rerun()
        else:
            st.error("Credenciales inválidas")

def guard_dashboard():
    st.title("🚧 Panel del Guardia")
    st.write(f"👤 Sesión iniciada como **{st.session_state['username']}**")
    if st.button("🔒 Cerrar Sesión"):
        st.session_state['session'].get('http://localhost:5000/logout')
        st.session_state.clear()
        st.experimental_rerun()

    st.markdown("---")
    st.header("📸 Procesar Imagen del Vehículo")
    # Subir imagen
    uploaded_image = st.file_uploader("Subir Imagen del Vehículo", type=["jpg", "png"])
    if uploaded_image is not None:
        image = Image.open(uploaded_image)
        st.image(image, caption='Imagen Subida', use_column_width=True)
        with st.spinner('Procesando imagen...'):
            # Enviar imagen al backend usando la sesión autenticada
            files = {'image': uploaded_image.getvalue()}
            try:
                response = st.session_state['session'].post('http://localhost:5000/guard/process_image', files=files)
                if response.ok:
                    data = response.json()
                    if data and isinstance(data, list):
                        for idx, item in enumerate(data):
                            if item['status'] == 'recognized':
                                person = item['person']
                                st.success("Vehículo reconocido")
                                st.write(f"**Nombre:** {person['name']}")
                                st.write(f"**Número de ID:** {person['id_number']}")
                                st.write(f"**Rol:** {person['role']}")
                                st.write(f"**Estado:** {person.get('flag', 'Sin infracciones')}")
                                # Mostrar foto
                                picture_path = os.path.join('static', 'images', person['picture'])
                                if os.path.exists(picture_path):
                                    st.image(picture_path, width=200)
                                # Opciones para emitir infracción o denegar acceso
                                st.markdown("### Acciones")
                                reason = st.text_area("Razón de la infracción", key=f"reason_{person['id_number']}_{idx}")
                                col1, col2 = st.columns(2)
                                with col1:
                                    if st.button("Emitir Infracción", key=f"ticket_{person['id_number']}_{idx}"):
                                        if reason:
                                            ticket_response = st.session_state['session'].post('http://localhost:5000/guard/issue_ticket', json={'person_id': person['id'], 'reason': reason})
                                            if ticket_response.ok:
                                                st.success("Infracción emitida")
                                            else:
                                                st.error("Error al emitir la infracción")
                                        else:
                                            st.warning("Por favor, ingrese la razón de la infracción.")
                                with col2:
                                    if st.button("Denegar Acceso", key=f"deny_{person['id_number']}_{idx}"):
                                        deny_response = st.session_state['session'].post('http://localhost:5000/guard/deny_access', json={'person_id': person['id']})
                                        if deny_response.ok:
                                            st.success("Acceso denegado")
                                        else:
                                            st.error("Error al denegar el acceso")
                            elif item['status'] == 'unrecognized_plate':
                                unique_key = f"{item['plate']}_{idx}"
                                st.warning(f"Placa no reconocida: {item['plate']}")
                                id_number = st.text_input("Ingrese el Número de ID para asociar la placa", key=f"id_input_{unique_key}")
                                if st.button("Asignar Placa", key=f"assign_button_{unique_key}"):
                                    assign_response = st.session_state['session'].post('http://localhost:5000/guard/assign_plate', json={'id_number': id_number, 'plate': item['plate']})
                                    if assign_response.ok:
                                        st.success("Placa asignada")
                                    else:
                                        st.error("Error al asignar la placa")
                            else:
                                st.warning("No se detectó ninguna placa")
                    else:
                        st.error("Respuesta inesperada del servidor.")
                else:
                    st.error("Error al procesar la imagen")
                    st.write("Estado de la respuesta:", response.status_code)
                    st.write("Contenido de la respuesta:", response.text)
            except Exception as e:
                st.error("Error al conectar con el servidor.")
                st.write(str(e))

def admin_dashboard():
    st.title("🚦 Panel de Administración")
    st.write(f"👤 Sesión iniciada como **{st.session_state['username']}**")
    if st.button("🔒 Cerrar Sesión"):
        st.session_state['session'].get('http://localhost:5000/logout')
        st.session_state.clear()
        st.experimental_rerun()

    st.markdown("---")
    st.header("📋 Gestionar Guardias")
    session = st.session_state['session']
    response = session.get('http://localhost:5000/api/admin/guards')
    if response.ok:
        guards = response.json().get('guards', [])
        for guard in guards:
            with st.expander(f"🔹 {guard['name']} - Puerta: {guard['gate_assigned']}"):
                # Opciones para eliminar o actualizar
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Eliminar", key=f"delete_{guard['id']}"):
                        delete_response = session.post(
                            'http://localhost:5000/admin/guards',
                            json={'action': 'remove', 'guard_id': guard['id']}
                        )
                        if delete_response.ok:
                            st.success("Guardia eliminado")
                            st.experimental_rerun()
                        else:
                            st.error("Error al eliminar el guardia")
                with col2:
                    new_gate = st.text_input("Nueva Puerta Asignada", key=f"gate_{guard['id']}")
                    if st.button("Actualizar Puerta", key=f"update_{guard['id']}"):
                        update_response = session.post(
                            'http://localhost:5000/admin/guards',
                            json={'action': 'update', 'guard_id': guard['id'], 'gate_assigned': new_gate}
                        )
                        if update_response.ok:
                            st.success("Puerta actualizada")
                            st.experimental_rerun()
                        else:
                            st.error("Error al actualizar la puerta")
    else:
        st.error("Error al obtener la lista de guardias")

    st.markdown("---")
    st.header("➕ Agregar Nuevo Guardia")
    with st.form(key='add_guard_form'):
        name = st.text_input("Nombre del Guardia", key='add_name')
        username = st.text_input("Usuario", key='add_username')
        password = st.text_input("Contraseña", type="password", key='add_password')
        gate_assigned = st.text_input("Puerta Asignada", key='add_gate')
        submitted = st.form_submit_button("Agregar Guardia")
        if submitted:
            add_response = session.post(
                'http://localhost:5000/admin/guards',
                json={
                    'action': 'add',
                    'name': name,
                    'username': username,
                    'password': password,
                    'gate_assigned': gate_assigned
                }
            )
            if add_response.ok:
                st.success("Guardia agregado")
                st.experimental_rerun()
            else:
                st.error("Error al agregar el guardia")

    st.markdown("---")
    st.header("🚗 Registros de Acceso")
    # Obtener registros de acceso
    access_logs_response = session.get('http://localhost:5000/api/admin/access_logs')
    if access_logs_response.ok:
        access_logs = access_logs_response.json().get('access_logs', [])
        if access_logs:
            for log in access_logs:
                st.write(f"**Placa:** {log['plate']}")
                st.write(f"**Nombre:** {log['person_name']}")
                st.write(f"**Acción:** {log['action']}")
                st.write(f"**Fecha y Hora:** {log['timestamp']}")
                st.write(f"**Guardia:** {log['guard_name']}")
                st.markdown("---")
        else:
            st.write("No hay registros de acceso.")
    else:
        st.error("Error al obtener los registros de acceso")

    st.markdown("---")
    st.header("🛡️ Logs de Guardias")
    # Obtener logs de guardias
    guard_logs_response = session.get('http://localhost:5000/api/admin/guard_logs')
    if guard_logs_response.ok:
        guard_logs = guard_logs_response.json().get('guard_logs', [])
        if guard_logs:
            for log in guard_logs:
                st.write(f"**Guardia:** {log['guard_name']}")
                st.write(f"**Acción:** {log['action']}")
                st.write(f"**Puerta:** {log['gate']}")
                st.write(f"**Fecha y Hora:** {log['timestamp']}")
                st.markdown("---")
        else:
            st.write("No hay logs de guardias.")
    else:
        st.error("Error al obtener los logs de guardias")

def main():
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False

    if not st.session_state['logged_in']:
        login()
    else:
        role = st.session_state.get('role')
        if role == 'guard':
            guard_dashboard()
        elif role == 'admin':
            admin_dashboard()
        else:
            st.error("Rol de usuario no reconocido")
            st.session_state.clear()
            st.experimental_rerun()

if __name__ == "__main__":
    main()


