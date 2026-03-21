from datetime import datetime, timezone, timedelta

import pytest

from bot.commands import parsear_fecha_ar

AR_TZ = timezone(timedelta(hours=-3))


def _fecha_futura_ar(horas=2) -> str:
    """Genera un string de fecha en hora argentina, en el futuro."""
    dt = datetime.now(AR_TZ) + timedelta(hours=horas)
    return dt.strftime("%d/%m/%Y %H:%M")


def test_parsear_fecha_valida_retorna_utc():
    fecha_str = _fecha_futura_ar(horas=2)
    result = parsear_fecha_ar(fecha_str)
    assert result.tzinfo == timezone.utc


def test_parsear_fecha_convierte_zona_horaria():
    # Una fecha fija: 01/06/2030 12:00 AR (UTC-3) → 15:00 UTC
    result = parsear_fecha_ar("01/06/2030 12:00")
    assert result.hour == 15
    assert result.minute == 0


def test_parsear_fecha_formato_invalido():
    with pytest.raises(ValueError, match="Formato de fecha inválido"):
        parsear_fecha_ar("2030-06-01 12:00")


def test_parsear_fecha_formato_invalido_sin_hora():
    with pytest.raises(ValueError, match="Formato de fecha inválido"):
        parsear_fecha_ar("01/06/2030")


def test_parsear_fecha_pasada():
    with pytest.raises(ValueError, match="debe ser en el futuro"):
        parsear_fecha_ar("01/01/2020 00:00")
