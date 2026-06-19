from sqlalchemy import Column, Integer, String, Boolean, func, DateTime
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
