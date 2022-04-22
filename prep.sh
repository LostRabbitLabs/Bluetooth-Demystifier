sudo systemctl enable postgresql
sudo systemctl start postgresql
sudo -u postgres createuser root
sudo -u postgres createdb blt
sudo python3 inc/models.py
sudo psql blt < data/import_all.sql
