#######################################################
# COMANDOS P.I.M.P xD
#######################################################
pip install wxpython==4.1.1
pip install --force-reinstall --no-cache-dir wxpython
pip cache purge

#######################################################
# MODO Flash
#######################################################
pip freeze > requirements.txt
pip freeze --no-version > requirements.txt

pip install -r requirements.txt
Exemplo de arquivo "requirements.txt":

httpx 
mysql.connector
random 
time 
ping3 
platform
requests 
datetime 
re
#######################################################
