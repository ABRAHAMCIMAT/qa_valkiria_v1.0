"""Semántica compartida de los pasos web: el código generado (HU-009) y el runner de Chromium (HU-010) hacen lo mismo."""

from valkiria.application.script_generation import generate_scripts
from valkiria.application.web_steps import anchors, key, plan_case

NO_STOCK = {"id": "TC-AC-02-N", "criterion_id": "AC-02", "type": "negative", "scenario": "Registrar orden con vehículo sin stock",
            "steps": ["Ir a la pantalla de registro de orden", "Seleccionar el vehículo \"Kicks 2025\"", "Hacer clic en el botón Registrar orden",
                      "Verificar que se muestra el mensaje \"Vehículo sin stock\""],
            "expected_result": "La orden es rechazada con el mensaje Vehículo sin stock", "data": {"vehiculo": "Kicks 2025"}}


def test_steps_translate_to_actions_fields_and_values():
    plan = plan_case(NO_STOCK)
    assert plan.route == "/ui/orders"  # la pantalla sale del escenario, no de la URL base
    # Aterrizado en el catálogo: el campo y el botón que existen en la pantalla (la apertura inicial ya no se repite).
    assert [(s.action, s.target) for s in plan.steps] == [("select", "Vehículo"), ("click", "Registrar orden"), ("check", "Vehículo")]
    assert (plan.steps[0].data_key, plan.steps[0].value) == ("vehiculo", "Kicks 2025")  # el valor sale del archivo de datos
    assert plan.steps[2].anchors == ["Vehículo sin stock"]
    assert (plan.anchors, plan.expect_error) == (["Vehículo sin stock"], True)


def test_expected_results_become_visible_texts_not_literal_sentences():
    assert anchors("Se muestra el mensaje de concesionario inactivo") == (["Concesionario inactivo"], True)
    assert anchors("La orden queda aprobada") == (["Orden aprobada"], False)
    assert anchors("Se muestra el Sentra disponible") == (["Sentra"], False)


def test_vague_and_cross_screen_steps_are_grounded_on_the_real_controls():
    # Pasos reales de Llama 3.2: vagos y mezclando la consulta de inventario con el registro de la orden.
    plan = plan_case({"id": "TC-AC-01-N", "scenario": "", "data": {"vehiculo": "Kicks 2025", "cliente": "Cliente Sintético 002"},
                      "steps": ["Abrir consultar inventario", "Buscar vehículo disponible", "Seleccionar vehículo y cliente", "Registrar orden de venta"],
                      "expected_result": "Mensaje de vehículo sin stock"})
    assert plan.route == "/ui"
    assert [(s.action, s.target, s.value) for s in plan.steps] == [
        ("click", "Buscar", None), ("open", "/ui/orders", None), ("select", "Vehículo", "Kicks 2025"),
        ("select", "Cliente", "Cliente Sintético 002"), ("click", "Registrar orden", None)]


def test_case_data_fills_the_form_before_submitting():
    plan = plan_case({"id": "TC-1", "scenario": "Registrar orden", "data": {"vehiculo": "Sentra", "total": "0"},
                      "steps": ["Ir a registrar orden", "Registrar orden de venta"], "expected_result": "Error: el total debe ser mayor a cero"})
    assert [(s.action, s.target, s.value) for s in plan.steps] == [("select", "Vehículo", "Sentra"), ("fill", "Total", "0"), ("click", "Registrar orden", None)]
    assert plan.anchors == ["El total debe ser mayor a cero"] and plan.expect_error


def test_unknown_controls_keep_the_literal_step_to_fail_visibly():
    plan = plan_case({"id": "TC-1", "scenario": "Exportar", "steps": ["Abrir la consulta de inventario", "Hacer clic en Exportar a Excel"]})
    assert [(s.action, s.target) for s in plan.steps] == [("click", "Exportar")]


def test_steps_that_name_the_value_find_the_field_through_the_case_data():
    plan = plan_case({"id": "TC-AC-04-N", "scenario": "", "data": {"concesionario": "Nissan Apodaca Sintético", "vehiculo": "Kicks 2025", "disponible": "Sí"},
                      "steps": ["Ir a registrar orden", "Seleccionar Nissan Apodaca Sintético", "Seleccionar Kicks 2025", "Seleccionar el color rojo"]})
    assert [(s.action, s.target, s.value) for s in plan.steps] == [
        ("select", "Concesionario", "Nissan Apodaca Sintético"), ("select", "Vehículo", "Kicks 2025"), ("select", "color rojo", "color rojo")]


def test_steps_that_name_a_known_option_find_its_list_without_case_data():
    plan = plan_case({"id": "TC-1", "scenario": "Consultar inventario", "data": {},
                      "steps": ["Abrir la consulta de inventario", "Seleccionar Nissan Centro Sintético", "Hacer clic en Buscar"],
                      "expected_result": "Se muestra el mensaje Concesionario inactivo"})
    assert [(s.action, s.target, s.value) for s in plan.steps] == [("select", "Concesionario", "Nissan Centro Sintético"), ("click", "Buscar", None)]
    orders = plan_case({"id": "TC-2", "scenario": "Registrar orden", "steps": ["Ir a registrar orden", "Seleccionar Kicks 2025"]})
    assert [(s.action, s.target, s.value) for s in orders.steps] == [("select", "Vehículo", "Kicks 2025")]


def test_the_option_named_in_the_step_is_used_when_the_case_has_no_data():
    # Caso real en vivo: sin datos, "Seleccionar Concesionario Nissan Centro Sintético" dejaba el concesionario por defecto (activo).
    plan = plan_case({"id": "TC-AC-01-N", "scenario": "", "data": {}, "steps": [
        "Abrir Consulta de inventario", "Seleccionar Concesionario Nissan Centro Sintético", "Marcar Solo disponibles", "Hacer clic en Buscar"],
        "expected_result": "Muestra mensaje Sin veh\u00cclculos disponibles"})  # texto con el error de codificación que produjo el modelo
    assert plan.steps[0].value == "Nissan Centro Sintético"
    assert plan.anchors == ["Sin vehículos disponibles"] and plan.expect_error


def test_checkboxes_and_long_targets():
    plan = plan_case({"id": "TC-1", "scenario": "Inventario", "steps": ["Marcar la casilla Solo disponibles", "Desmarcar la casilla Solo disponibles"]})
    assert [(s.action, s.target, s.value) for s in plan.steps] == [("tick", "Solo disponibles", "on"), ("tick", "Solo disponibles", "off")]
    assert key("Seleccionar el concesionario de la lista de agencias") == "concesionario"
    assert plan_case({"id": "x", "scenario": "Consultar citas de servicio", "steps": []}).route == "/ui/appointments"


def test_generated_playwright_and_selenium_follow_the_same_plan():
    files = generate_scripts([NO_STOCK], framework="playwright", platform="web", feature="Ordenes")
    spec = files["tests/specs/tc_ac_02_n.spec.ts"]
    assert 'app.open("/ui/orders")' in spec
    assert 'app.select("Vehículo", String(data["vehiculo"] ?? "Kicks 2025"))' in spec
    assert 'app.click("Registrar orden")' in spec
    assert 'app.expectResult(["Vehículo sin stock"], true)' in spec
    page = files["tests/pages/OrdenesPage.ts"]
    assert "getByRole('button'" in page and "[role=alert].error" in page  # botón antes que enlace; aviso de error por convención
    selenium = generate_scripts([NO_STOCK], framework="selenium", platform="web", feature="Ordenes")["tests/test_tc_ac_02_n.py"]
    assert 'page.select("Vehículo", str(data.get("vehiculo", "Kicks 2025")))' in selenium
    assert 'page.expect_result(["Vehículo sin stock"], True)' in selenium
    for code in (*files.values(), selenium):
        assert "BASE_URL ?? '/'" not in code  # ya no abre la raíz del sitio a ciegas
