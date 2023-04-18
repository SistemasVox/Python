import mysql.connector

# Função para estabelecer conexão com o servidor MySQL
def get_connection(config):
    try:
        conn = mysql.connector.connect(**config)
        conn.ping(reconnect=True)
        return conn
    except mysql.connector.Error as e:
        print(f"Erro ao conectar ao servidor MySQL: {e}")
        return None

# Função para obter a lista de concursos do servidor MySQL
def get_concursos(conn):
    cursor = conn.cursor()
    cursor.execute('SELECT concurso FROM loto ORDER BY concurso')
    return [row[0] for row in cursor.fetchall()]

# Função para copiar dados ausentes do servidor externo para o servidor local
def copy_data(local_conn, external_conn, ausentes_no_local):
    local_cursor = local_conn.cursor()
    external_cursor = external_conn.cursor()

    num_rows_inserted = 0
    for concurso in ausentes_no_local:
        try:
            external_cursor.execute(f'SELECT * FROM loto WHERE concurso = {concurso}')
            data = external_cursor.fetchone()
            insert_query = f"INSERT INTO loto (concurso, data_concurso, dezenas) VALUES (%s, %s, %s)"
            local_cursor.execute(insert_query, data)
            num_rows_inserted += local_cursor.rowcount
            print(f"Concurso {concurso} inserido no servidor local.")
        except Exception as e:
            print(f"Erro ao inserir os dados do concurso {concurso}: {e}")

    local_conn.commit()
    local_cursor.close()
    external_cursor.close()
    return num_rows_inserted

# Configurações de conexão com os servidores MySQL
local_config = {
    # ...
}

external_config = {
    # ...
}

# Estabelecer conexão com os servidores MySQL
local_conn = get_connection(local_config)
external_conn = get_connection(external_config)

if local_conn and external_conn:
    print("Conexão estabelecida com sucesso em ambos os servidores MySQL.")

    # Obter a lista de concursos de cada servidor
    local_concursos = get_concursos(local_conn)
    external_concursos = get_concursos(external_conn)

    # Identificar os concursos ausentes no servidor local
    ausentes_no_local = [concurso for concurso in external_concursos if concurso not in local_concursos]

    # Copiar os dados ausentes do servidor externo para o servidor local
    if ausentes_no_local:
        num_rows_inserted = copy_data(local_conn, external_conn, ausentes_no_local)
        print(f"Total de {num_rows_inserted} linhas inseridas no servidor local.")
    else:
        print("Não há necessidade de atualizar, ambos atualizados.")

    # Fechar as conexões com os servidores MySQL
    local_conn.close()
    external_conn.close()

else:
    print("Não foi possível estabelecer conexão com ambos os servidores MySQL.")
