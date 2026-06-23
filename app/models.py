from sqlalchemy import Column, Integer, SmallInteger, String, Boolean, func, DateTime, ForeignKey, Date, UniqueConstraint, Text
from sqlalchemy.orm import DeclarativeBase
from .database import Base


class Klub(Base):
    __tablename__ = "klubovi"

    id             = Column(Integer, primary_key=True)
    naziv_kluba    = Column(String(150), nullable=False, unique=True)
    username       = Column(String(80),  nullable=False, unique=True)
    password_hash  = Column(String(255), nullable=False)
    email          = Column(String(150), nullable=False, unique=True)
    kontakt_osoba  = Column(String(150))
    mobitel        = Column(String(20))
    grad           = Column(String(100))
    aktivan        = Column(Boolean, nullable=False, default=True)
    logo           = Column(String(100), nullable=True)
    kreiran_datum  = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    azuriran_datum = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Admin(Base):
    __tablename__ = "admini"

    id             = Column(Integer, primary_key=True)
    username       = Column(String(80),  nullable=False, unique=True)
    password_hash  = Column(String(255), nullable=False)
    email          = Column(String(150), nullable=False, unique=True)
    ime            = Column(String(100), nullable=False)
    prezime        = Column(String(100), nullable=False)
    uloga          = Column(String(20),  nullable=False, default="moderator")
    aktivan        = Column(Boolean, nullable=False, default=True)
    kreiran_datum  = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    azuriran_datum = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Takmicenje(Base):
    __tablename__ = "takmicenja"

    id            = Column(Integer, primary_key=True)
    naziv         = Column(String(150), nullable=False)
    opis          = Column(String(500), nullable=True)
    aktivan       = Column(Boolean, nullable=False, default=True)
    kreiran_datum = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Sezona(Base):
    __tablename__ = "sezone"

    id             = Column(Integer, primary_key=True)
    naziv          = Column(String(100), nullable=False)
    takmicenje_id  = Column(Integer, ForeignKey("takmicenja.id"), nullable=False)
    datum_od       = Column(Date, nullable=True)
    datum_do       = Column(Date, nullable=True)
    aktivna        = Column(Boolean, nullable=False, default=True)
    kreiran_datum  = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Uzrast(Base):
    __tablename__ = "uzrasti"

    id             = Column(Integer, primary_key=True)
    naziv          = Column(String(100), nullable=False)
    sezona_id      = Column(Integer, ForeignKey("sezone.id"), nullable=False)
    takmicenje_id  = Column(Integer, ForeignKey("takmicenja.id"), nullable=False)
    aktivan        = Column(Boolean, nullable=False, default=True)
    kreiran_datum  = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PrijavaKluba(Base):
    __tablename__ = "prijave_klubova"

    id                = Column(Integer, primary_key=True)
    klub_id           = Column(Integer, ForeignKey("klubovi.id"), nullable=False)
    uzrast_id         = Column(Integer, ForeignKey("uzrasti.id"), nullable=False)
    status            = Column(String(20), nullable=False, default="prijavljen")
    napomena          = Column(String(500), nullable=True)
    prijavljen_datum  = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Igrac(Base):
    __tablename__ = "igraci"

    id                 = Column(Integer, primary_key=True)
    ime                = Column(String(100), nullable=False)
    prezime            = Column(String(100), nullable=False)
    datum_rodjenja     = Column(Date, nullable=False)
    drzavljanstvo      = Column(String(100), nullable=False, default="Bosna i Hercegovina")
    trenutni_klub_id   = Column(Integer, ForeignKey("klubovi.id"), nullable=True)
    prethodni_klub_id  = Column(Integer, ForeignKey("klubovi.id"), nullable=True)
    status             = Column(String(20), nullable=False, default="aktivan")
    kreiran_datum      = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Registracija(Base):
    __tablename__ = "registracije"

    id               = Column(Integer, primary_key=True)
    igrac_id         = Column(Integer, ForeignKey("igraci.id"), nullable=False)
    klub_id          = Column(Integer, ForeignKey("klubovi.id"), nullable=False)
    sezona_id        = Column(Integer, ForeignKey("sezone.id"), nullable=False)
    br_registracije  = Column(String(50), nullable=True, unique=True)
    status           = Column(String(20), nullable=False, default="na_cekanju")
    napomena         = Column(String(500), nullable=True)
    kreiran_datum    = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    odobren_datum    = Column(DateTime(timezone=True), nullable=True)
    nevazeca_datum   = Column(DateTime(timezone=True), nullable=True)


class PozicijaSL(Base):
    __tablename__ = "pozicije_sl"

    id      = Column(Integer, primary_key=True)
    naziv   = Column(String(100), nullable=False, unique=True)
    aktivan = Column(Boolean, nullable=False, default=True)


class SluzbenoLice(Base):
    __tablename__ = "sluzbena_lica"

    id                = Column(Integer, primary_key=True)
    ime               = Column(String(100), nullable=False)
    prezime           = Column(String(100), nullable=False)
    datum_rodjenja    = Column(Date, nullable=True)
    mjesto            = Column(String(150), nullable=True)
    trenutni_klub_id  = Column(Integer, ForeignKey("klubovi.id"), nullable=True)
    prethodni_klub_id = Column(Integer, ForeignKey("klubovi.id"), nullable=True)
    pozicija_id       = Column(Integer, ForeignKey("pozicije_sl.id"), nullable=True)
    status            = Column(String(20), nullable=False, default="aktivan")
    kreiran_datum     = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RegistracijaSL(Base):
    __tablename__ = "registracije_sl"

    id               = Column(Integer, primary_key=True)
    sluzbeno_lice_id = Column(Integer, ForeignKey("sluzbena_lica.id"), nullable=False)
    klub_id          = Column(Integer, ForeignKey("klubovi.id"), nullable=False)
    sezona_id        = Column(Integer, ForeignKey("sezone.id"), nullable=False)
    br_registracije  = Column(String(50), nullable=True, unique=True)
    status           = Column(String(20), nullable=False, default="na_cekanju")
    napomena         = Column(String(500), nullable=True)
    kreiran_datum    = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    odobren_datum    = Column(DateTime(timezone=True), nullable=True)
    nevazeca_datum   = Column(DateTime(timezone=True), nullable=True)


# ─── TABELE TAKMIČENJA ───────────────────────────────────────────────────────

class Tabela(Base):
    """Liga tabela — vezana za uzrast (koji nosi sezona + takmicenje).
    Opcionalna 'grupa' za raspored po grupama (A, B, ...)."""
    __tablename__ = "tabele"
    __table_args__ = (UniqueConstraint("uzrast_id", "grupa", name="uq_tabela_uzrast_grupa"),)

    id                = Column(Integer, primary_key=True)
    naziv             = Column(String(200), nullable=False)
    uzrast_id         = Column(Integer, ForeignKey("uzrasti.id"), nullable=False)
    grupa             = Column(String(50), nullable=True)          # npr. "A", "B", ili NULL
    bodovi_pobjeda    = Column(Integer, nullable=False, default=2)
    bodovi_nerjeseno  = Column(Integer, nullable=False, default=1)
    bodovi_poraz      = Column(Integer, nullable=False, default=0)
    aktivan           = Column(Boolean, nullable=False, default=True)
    kreiran_datum     = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TabelaEkipa(Base):
    """Ekipa (klub) dodata u tabelu — uvijek ref na odobrenu prijavu."""
    __tablename__ = "tabela_ekipe"
    __table_args__ = (UniqueConstraint("tabela_id", "prijava_id", name="uq_te_tabela_prijava"),)

    id               = Column(Integer, primary_key=True)
    tabela_id        = Column(Integer, ForeignKey("tabele.id"), nullable=False)
    prijava_id       = Column(Integer, ForeignKey("prijave_klubova.id"), nullable=False)
    seed_broj        = Column(Integer, nullable=True)              # žrijeb: 1..N
    bonus_bodovi     = Column(Integer, nullable=False, default=0)
    kazneni_bodovi   = Column(Integer, nullable=False, default=0)
    aktivan          = Column(Boolean, nullable=False, default=True)


class Utakmica(Base):
    """Odigrana ili zakazana utakmica unutar tabele."""
    __tablename__ = "utakmice"

    id              = Column(Integer, primary_key=True)
    tabela_id       = Column(Integer, ForeignKey("tabele.id"), nullable=False)
    domacin_id      = Column(Integer, ForeignKey("prijave_klubova.id"), nullable=False)
    gost_id         = Column(Integer, ForeignKey("prijave_klubova.id"), nullable=True)   # NULL za BYE
    je_bye          = Column(Boolean, nullable=False, default=False)  # slobodna ekipa
    gol_domacin     = Column(Integer, nullable=True)    # NULL = nije odigrana
    gol_gost        = Column(Integer, nullable=True)
    odigrana        = Column(Boolean, nullable=False, default=False)
    kolo            = Column(Integer, nullable=True)
    datum_utakmice  = Column(DateTime(timezone=True), nullable=True)
    napomena        = Column(String(300), nullable=True)
    kreiran_datum   = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TabelaSortPravilo(Base):
    """Pravila sortiranja tabele (prioritet 1 = najvažniji).
    kriterij: bodovi | gol_razlika | dati_golovi | primljeni_golovi | pobjede | porazi | medjusobno_bodovi | medjusobno_gol_razlika
    smjer: DESC | ASC
    """
    __tablename__ = "tabela_sort_pravila"
    __table_args__ = (UniqueConstraint("tabela_id", "prioritet", name="uq_sort_tabela_prioritet"),)

    id          = Column(Integer, primary_key=True)
    tabela_id   = Column(Integer, ForeignKey("tabele.id"), nullable=False)
    kriterij    = Column(String(50), nullable=False)   # bodovi, gol_razlika, ...
    prioritet   = Column(Integer, nullable=False)      # 1 = najvažniji
    smjer       = Column(String(4), nullable=False, default="DESC")  # DESC | ASC
    aktivan     = Column(Boolean, nullable=False, default=True)


# ─────────────────────────────────────────────────────────────
#  MINI RUKOMET
# ─────────────────────────────────────────────────────────────

class MiniRukometTurnir(Base):
    """Turnir / grupa za Mini rukomet."""
    __tablename__ = "mini_rukomet_turnir"

    id            = Column(Integer, primary_key=True)
    naziv         = Column(String(200), nullable=False)
    opis          = Column(Text, nullable=True)
    aktivan       = Column(Boolean, nullable=False, default=True)
    # Kriteriji rangiranja (sort1 = primarni, sort2 = sekundarni, sort3 = tercijarni)
    # Mogućnosti: bodovi | gol_razlika | gol_postignuti | gol_primljeni | pobjede | porazi | utakmice
    sort1         = Column(String(50), nullable=False, default="bodovi")
    sort2         = Column(String(50), nullable=False, default="gol_razlika")
    sort3         = Column(String(50), nullable=False, default="gol_postignuti")
    kreiran_datum = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MiniRukometUtakmica(Base):
    """Utakmica Mini rukometa."""
    __tablename__ = "mini_rukomet_utakmica"

    id             = Column(Integer, primary_key=True)
    turnir_id      = Column(Integer, ForeignKey("mini_rukomet_turnir.id"), nullable=False)
    datum_utakmice = Column(DateTime(timezone=True), nullable=True)
    ekipa_a        = Column(String(200), nullable=False)
    ekipa_b        = Column(String(200), nullable=False)
    gol_a          = Column(SmallInteger, nullable=True)
    gol_b          = Column(SmallInteger, nullable=True)
    kolo           = Column(SmallInteger, nullable=True)
    kreiran_datum  = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MiniRukometPrijava(Base):
    """Prijava kluba na Mini rukomet turnir."""
    __tablename__ = "mini_rukomet_prijava"

    id           = Column(Integer, primary_key=True)
    turnir_id    = Column(Integer, ForeignKey("mini_rukomet_turnir.id"), nullable=False)
    klub_id      = Column(Integer, ForeignKey("klubovi.id"), nullable=False)
    naziv_ekipe  = Column(String(200), nullable=False)  # Naziv ekipe u turniru
    status       = Column(String(20), nullable=False, default="na_cekanju")  # na_cekanju / odobren
    kreiran_datum = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ─────────────────────────────────────────────────────────────
#  ZAPISNIK UTAKMICE
# ─────────────────────────────────────────────────────────────

class ZapisnikUtakmica(Base):
    """Zvanični zapisnik rukomet utakmice."""
    __tablename__ = "zapisnik_utakmica"

    id                     = Column(Integer, primary_key=True)
    br_utakmice            = Column(String(30), unique=True, nullable=True)
    datum                  = Column(Date, nullable=True)
    vrijeme                = Column(String(10), nullable=True)          # HH:MM
    kolo                   = Column(SmallInteger, nullable=True)
    uzrast                 = Column(String(50), nullable=True)
    liga                   = Column(String(200), nullable=True)
    ekipa_a                = Column(String(200), nullable=True)
    ekipa_b                = Column(String(200), nullable=True)
    ekipa_a_id             = Column(Integer, ForeignKey("klubovi.id"), nullable=True)
    ekipa_b_id             = Column(Integer, ForeignKey("klubovi.id"), nullable=True)
    sudija_a               = Column(String(200), nullable=True)
    sudija_b               = Column(String(200), nullable=True)
    delegat                = Column(String(200), nullable=True)
    zapisnicar             = Column(String(200), nullable=True)
    mjerilac_vremena       = Column(String(200), nullable=True)
    glavni_sluzbeni        = Column(String(200), nullable=True)
    ljekar                 = Column(String(200), nullable=True)
    status_utakmice        = Column(String(50), nullable=False, default="Zakazana")
    verifikacija_sudija_a  = Column(Boolean, nullable=False, default=False)
    verifikacija_sudija_b  = Column(Boolean, nullable=False, default=False)
    verifikacija_delegat   = Column(Boolean, nullable=False, default=False)
    zadnje_spasio          = Column(String(200), nullable=True)
    zadnje_izmijenjeno     = Column(DateTime(timezone=True), nullable=True)
    kreiran_datum          = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ZapisnikIgrac(Base):
    """Igrač ili službeno lice u zapisniku utakmice."""
    __tablename__ = "zapisnik_igrac"

    id                 = Column(Integer, primary_key=True)
    zapisnik_id        = Column(Integer, ForeignKey("zapisnik_utakmica.id"), nullable=False)
    tim                = Column(String(1), nullable=False)              # A | B
    tip                = Column(String(20), nullable=False, default="igrac")  # igrac | sluzbeno_lice
    pozicija           = Column(String(50), nullable=True)
    br_dresa           = Column(SmallInteger, nullable=True)
    ime_prezime        = Column(String(200), nullable=False)
    br_registracije    = Column(String(50), nullable=True)
    golovi             = Column(SmallInteger, nullable=False, default=0)
    opomene            = Column(SmallInteger, nullable=False, default=0)
    iskljucenje        = Column(SmallInteger, nullable=False, default=0)
    iskljucenje_1      = Column(SmallInteger, nullable=False, default=0)
    iskljucenje_2      = Column(SmallInteger, nullable=False, default=0)
    crveni_karton      = Column(Boolean, nullable=False, default=False)
    plavi_karton       = Column(Boolean, nullable=False, default=False)
    time_out_1         = Column(SmallInteger, nullable=False, default=0)
    time_out_2         = Column(SmallInteger, nullable=False, default=0)
    zadnje_spasio      = Column(String(200), nullable=True)
    zadnje_izmijenjeno = Column(DateTime(timezone=True), nullable=True)

