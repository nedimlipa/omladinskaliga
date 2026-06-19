@echo off
echo === SSH Tunel: lokalno 5433 -> server PostgreSQL 5432 ===
echo Otvara tunel na localhost:5433
echo Pritisni CTRL+C da zatvori tunel
echo.
ssh -N -L 5433:localhost:5432 root@5.189.159.223
