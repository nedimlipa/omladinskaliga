from sqlalchemy import Column, Integer, String, Boolean, func, DateTime, ForeignKey, Date
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
