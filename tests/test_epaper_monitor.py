import epaper_monitor as em

HEADER = ("link,data_age_ms,notify_per_s,recording,t_s,rpm,mph,pitch_deg,roll_deg,batt_pct,batt_c,"
          "amps,volts,ctrl_c,motor_c,state,error_flags,esp_mv\n")


def test_parse_row_maps_header_and_converts_numbers():
    row = em.parse_row(em.parse_header(HEADER), "LIVE,120,455,1,62.345,800,9.92,-2.2,,75,25,-1.25,,28,30,3,0,4166\n")
    assert row["link"] == "LIVE"
    assert row["data_age_ms"] == 120
    assert row["mph"] == 9.92
    assert row["pitch_deg"] == -2.2
    assert row["roll_deg"] is None
    assert row["recording"] is True


def test_parse_row_tolerates_truncated_payload():
    row = em.parse_row(em.parse_header(HEADER), "LIVE,120,455")
    assert row["notify_per_s"] == 455
    assert row["mph"] is None


def test_render_shows_link_age_and_rate():
    row = em.parse_row(em.parse_header(HEADER), "STALE,7300,0,0,9.1,,,,,58,24,,,29,26,0,0,4164")
    line = em.render(row)
    assert "STALE" in line
    assert "age=7.3s" in line
    assert "n/s=0" in line
    assert "mph=?" in line
    assert "esp=4.16V" in line
    assert "battT=24C" in line
