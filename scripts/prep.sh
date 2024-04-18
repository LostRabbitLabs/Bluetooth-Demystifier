sudo systemctl enable postgresql
sudo systemctl start postgresql
sudo -i -u postgres createuser root
sudo -i -u postgres createdb blt
sudo -i -u postgres psql -d blt -c "GRANT ALL ON SCHEMA public TO root;"
sudo python3 backend/models.py
sudo psql blt < data/import_all.sql
