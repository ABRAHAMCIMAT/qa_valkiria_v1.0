INSERT INTO vehicles VALUES (1, 'Sentra', 2025, 289900.00, 10), (2, 'Kicks', 2025, 355000.00, 0), (3, 'Versa', 2024, 245000.00, 3);
INSERT INTO dealers VALUES (1, 'Nissan Apodaca Sintético', 'Nuevo León', TRUE), (2, 'Nissan Centro Sintético', 'Nuevo León', FALSE);
INSERT INTO customers VALUES (1, 'Cliente Sintético 001', 'cliente001@example.test'), (2, 'Cliente Sintético 002', 'cliente002@example.test');
INSERT INTO inventory VALUES (1, 1, 1, TRUE), (2, 2, 1, FALSE), (3, 3, 2, TRUE);
INSERT INTO sales_orders VALUES (1, 1, 1, 1, 'approved', 289900.00), (2, 1, 2, 2, 'cancelled', 355000.00);
INSERT INTO service_appointments VALUES (1, 1, 1, 'scheduled');
INSERT INTO parts VALUES (1, 'Filtro sintético', 25), (2, 'Balata sintética', 0);
INSERT INTO test_cases VALUES ('HU011-TC-001', 'Consultar vehículos disponibles', 'Devuelve Sentra y Versa');
