import streamlit as st
import pandas as pd
import requests
from io import StringIO
from datetime import datetime
import psycopg2
import os

# --- Configurações e Constantes ---
APP_TITLE = "Controle de Estoque e Caixa (DB Compartilhado)"
GITHUB_CSV_URL = "https://raw.githubusercontent.com/EricCamachoDM/caixa_festa/refs/heads/main/produtos_estoque.csv"

try:
    DATABASE_URL = st.secrets["DATABASE_URL"]
except FileNotFoundError: # Para rodar localmente sem secrets.toml, pode pegar de var de ambiente
    DATABASE_URL = os.environ.get("DATABASE_URL", "postgres://user:password@host:port/dbname_placeholder")
    if "dbname_placeholder" in DATABASE_URL:
        st.error("DATABASE_URL não configurada como segredo do Streamlit ou variável de ambiente.")
        st.stop() # Impede a execução se o BD não estiver configurado
except KeyError: # Se secrets.toml existe mas não tem DATABASE_URL
    st.error("A chave 'DATABASE_URL' não foi encontrada nos segredos do Streamlit (secrets.toml).")
    st.stop()


# --- Funções de Banco de Dados ---
def conectar_bd_pg():
    """Conecta ao banco de dados PostgreSQL e retorna a conexão."""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def criar_tabelas_se_nao_existirem_pg(conn): # Adicionei _pg para clareza
    """Cria as tabelas do banco de dados PostgreSQL se elas não existirem."""
    with conn.cursor() as cursor: # Usar 'with' para garantir que o cursor feche
        # Tabela de Produtos (SERIAL para autoincremento no PostgreSQL)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS produtos (
                id SERIAL PRIMARY KEY,
                nome TEXT UNIQUE NOT NULL,
                valor REAL NOT NULL,
                quantidade_estoque INTEGER NOT NULL
            )
        ''')
        # Tabela de Vendas (TIMESTAMP WITH TIME ZONE é bom para PostgreSQL)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vendas (
                id SERIAL PRIMARY KEY,
                horario TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
                valor_total REAL NOT NULL
            )
        ''')
        # Tabela de Itens da Venda
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS itens_venda (
                id SERIAL PRIMARY KEY,
                venda_id INTEGER NOT NULL,
                produto_id INTEGER NOT NULL,
                quantidade_vendida INTEGER NOT NULL,
                valor_unitario_momento_venda REAL NOT NULL,
                FOREIGN KEY (venda_id) REFERENCES vendas(id) ON DELETE CASCADE,
                FOREIGN KEY (produto_id) REFERENCES produtos(id) ON DELETE RESTRICT
            )
        ''')
    conn.commit() # Commit fora do 'with cursor'

@st.cache_data(ttl=3600) # Cache do CSV por 1 hora para não buscar toda hora no GitHub
def carregar_produtos_csv_do_github(url: str) -> pd.DataFrame | None:
    """Carrega os dados dos produtos de um arquivo CSV no GitHub."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        df = pd.read_csv(StringIO(response.text))
        # Validação básica das colunas esperadas
        if not all(col in df.columns for col in ["nome", "valor", "quantidade"]):
            st.error("O arquivo CSV não contém as colunas esperadas: 'nome', 'valor', 'quantidade'.")
            return None
        df['valor'] = pd.to_numeric(df['valor'], errors='coerce')
        df['quantidade'] = pd.to_numeric(df['quantidade'], errors='coerce')
        df.dropna(subset=['nome', 'valor', 'quantidade'], inplace=True)
        return df
    except requests.exceptions.RequestException as e:
        st.error(f"Erro ao buscar dados do GitHub: {e}")
        return None
    except Exception as e:
        st.error(f"Erro ao processar o arquivo CSV: {e}")
        return None

def sincronizar_csv_com_bd_pg(conn, url_csv: str):
    df_produtos_csv = carregar_produtos_csv_do_github(url_csv)
    if df_produtos_csv is None:
        st.error("Não foi possível carregar produtos do CSV para sincronização com o BD.")
        return

    produtos_atualizados = 0
    produtos_inseridos = 0

    with conn.cursor() as cursor:
        for _, row in df_produtos_csv.iterrows():
            nome_produto_csv = row['nome']
            valor_produto_csv = row['valor']
            quantidade_produto_csv = int(row['quantidade'])

            cursor.execute("SELECT id, valor, quantidade_estoque FROM produtos WHERE nome = %s", (nome_produto_csv,)) # %s para psycopg2
            produto_existente_row = cursor.fetchone()

            if produto_existente_row:
                # Convertendo a tupla do fetchone para um dict simulado para manter a lógica
                produto_existente = {"id": produto_existente_row[0], "valor": produto_existente_row[1], "quantidade_estoque": produto_existente_row[2]}
                if produto_existente["valor"] != valor_produto_csv or produto_existente["quantidade_estoque"] != quantidade_produto_csv:
                    cursor.execute(
                        "UPDATE produtos SET valor = %s, quantidade_estoque = %s WHERE nome = %s",
                        (valor_produto_csv, quantidade_produto_csv, nome_produto_csv)
                    )
                    produtos_atualizados +=1
            else:
                cursor.execute(
                    "INSERT INTO produtos (nome, valor, quantidade_estoque) VALUES (%s, %s, %s)",
                    (nome_produto_csv, valor_produto_csv, quantidade_produto_csv)
                )
                produtos_inseridos +=1
    conn.commit()
    # ... (mensagens e limpeza de cache como antes) ...
    msg = []
    if produtos_inseridos > 0: msg.append(f"{produtos_inseridos} novo(s) produto(s) inserido(s)")
    if produtos_atualizados > 0: msg.append(f"{produtos_atualizados} produto(s) existente(s) atualizado(s)")
    if not msg: msg.append("Nenhuma alteração nos produtos do BD pela sincronização.")
    st.success(f"Sincronização de produtos do CSV com o BD concluída. {'; '.join(msg)}")
    get_produtos_do_bd_pg.clear()
    get_caixa_total_do_bd_pg.clear()
    get_estoque_atual_do_bd_pg.clear()
    get_vendas_do_bd_pg.clear()


# --- Funções de Negócio (Adaptadas para Banco de Dados) ---
@st.cache_data(show_spinner="Buscando produtos...")
def get_produtos_do_bd_pg(conn) -> list:
    produtos = []
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor: # DictCursor para acessar colunas por nome
        cursor.execute("SELECT id, nome, valor, quantidade_estoque FROM produtos ORDER BY nome")
        for row in cursor.fetchall():
            produtos.append(dict(row))
    return produtos

def adicionar_produto_bd_pg(conn, nome: str, valor: float, quantidade: int):
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO produtos (nome, valor, quantidade_estoque) VALUES (%s, %s, %s)",
                (nome, valor, quantidade)
            )
        conn.commit()
        st.success(f"Produto '{nome}' adicionado ao banco de dados.")
        get_produtos_do_bd_pg.clear()
    except psycopg2.Error as e: # Erro específico do psycopg2 (ex: IntegrityError para UNIQUE)
        conn.rollback()
        if e.pgcode == '23505': # Código de erro para violação de UNIQUE
             st.error(f"Produto '{nome}' já existe no banco de dados.")
        else:
            st.error(f"Erro ao adicionar produto (PG): {e}")


def deletar_produto_bd_pg(conn, nome_produto: str):
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
            cursor.execute("SELECT id FROM produtos WHERE nome = %s", (nome_produto,))
            produto_info = cursor.fetchone()
            if not produto_info:
                st.error(f"Produto '{nome_produto}' não encontrado para deleção.")
                return

            produto_id = produto_info['id']
            cursor.execute("SELECT COUNT(*) FROM itens_venda WHERE produto_id = %s", (produto_id,))
            if cursor.fetchone()[0] > 0:
                st.error(f"Não é possível deletar o produto '{nome_produto}', pois ele está associado a vendas registradas.")
                return

            cursor.execute("DELETE FROM produtos WHERE nome = %s", (nome_produto,))
            rowcount = cursor.rowcount # psycopg2 cursor tem rowcount
        conn.commit()
        if rowcount > 0:
            st.success(f"Produto '{nome_produto}' deletado do banco de dados.")
            get_produtos_do_bd_pg.clear()
        else:
            st.warning(f"Produto '{nome_produto}' não encontrado para deleção (ou já deletado).")
    except psycopg2.Error as e:
        conn.rollback()
        st.error(f"Erro ao deletar produto (PG): {e}")


def registrar_venda_bd_pg(conn, produtos_venda_dict: dict) -> tuple[int | None, float]:
    valor_total_venda = 0.0
    itens_para_inserir_na_venda = []
    venda_id = None

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor: # BEGIN é implícito, ou conn.autocommit = False
            for nome_produto, quantidade_vendida in produtos_venda_dict.items():
                if quantidade_vendida <= 0: continue

                cursor.execute("SELECT id, nome, valor, quantidade_estoque FROM produtos WHERE nome = %s FOR UPDATE", (nome_produto,)) # FOR UPDATE para bloqueio de linha
                produto_db = cursor.fetchone()

                if not produto_db:
                    st.error(f"Produto '{nome_produto}' não encontrado no BD. Venda cancelada.")
                    raise Exception("Produto não encontrado") # Para acionar o rollback

                if produto_db["quantidade_estoque"] < quantidade_vendida:
                    st.error(f"Estoque insuficiente para '{nome_produto}'. Disponível: {produto_db['quantidade_estoque']}. Venda cancelada.")
                    raise Exception("Estoque insuficiente") # Para acionar o rollback

                novo_estoque = produto_db["quantidade_estoque"] - quantidade_vendida
                cursor.execute(
                    "UPDATE produtos SET quantidade_estoque = %s WHERE id = %s",
                    (novo_estoque, produto_db["id"])
                )
                valor_item_total = quantidade_vendida * produto_db["valor"]
                valor_total_venda += valor_item_total
                itens_para_inserir_na_venda.append({
                    "produto_id": produto_db["id"],
                    "quantidade_vendida": quantidade_vendida,
                    "valor_unitario_momento_venda": produto_db["valor"]
                })

            if not itens_para_inserir_na_venda:
                st.warning("Nenhum item válido na venda. Venda cancelada.")
                raise Exception("Nenhum item válido")

            horario_atual = datetime.now()
            cursor.execute(
                "INSERT INTO vendas (horario, valor_total) VALUES (%s, %s) RETURNING id", # RETURNING id para pegar o ID
                (horario_atual, valor_total_venda)
            )
            venda_id = cursor.fetchone()['id']

            for item in itens_para_inserir_na_venda:
                cursor.execute('''
                    INSERT INTO itens_venda (venda_id, produto_id, quantidade_vendida, valor_unitario_momento_venda)
                    VALUES (%s, %s, %s, %s)
                ''', (venda_id, item["produto_id"], item["quantidade_vendida"], item["valor_unitario_momento_venda"]))
        conn.commit()
        get_produtos_do_bd_pg.clear()
        get_caixa_total_do_bd_pg.clear()
        get_estoque_atual_do_bd_pg.clear()
        get_vendas_do_bd_pg.clear()
        return venda_id, valor_total_venda
    except Exception as e:
        conn.rollback()
        # Não mostrar st.error aqui se já foi mostrado antes (produto não encontrado, estoque insuficiente)
        if str(e) not in ["Produto não encontrado", "Estoque insuficiente", "Nenhum item válido"]:
             st.error(f"Erro crítico ao registrar venda (PG): {e}. A transação foi revertida.")
        return None, 0.0


@st.cache_data(show_spinner="Buscando histórico de vendas...")
def get_vendas_do_bd_pg(conn) -> list:
    vendas = []
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
        query = """
            SELECT
                v.id AS venda_id,
                v.horario,
                v.valor_total,
                STRING_AGG(p.nome || ' (Qtd: ' || iv.quantidade_vendida || ', Vlr Unit: R$' || TO_CHAR(iv.valor_unitario_momento_venda, 'FM999999990.00') || ')', '; ') AS produtos_detalhados
            FROM vendas v
            LEFT JOIN itens_venda iv ON v.id = iv.venda_id
            LEFT JOIN produtos p ON iv.produto_id = p.id
            GROUP BY v.id, v.horario, v.valor_total
            ORDER BY v.horario DESC
        """ # STRING_AGG para PostgreSQL, TO_CHAR para formatar valor
        cursor.execute(query)
        for row_dict in cursor.fetchall():
            vendas.append(dict(row_dict)) # Já vem como DictRow por causa do DictCursor
    return vendas

def deletar_venda_bd_pg(conn, venda_id_para_deletar: int):
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
            cursor.execute("""
                SELECT produto_id, quantidade_vendida
                FROM itens_venda
                WHERE venda_id = %s
            """, (venda_id_para_deletar,))
            itens_da_venda = cursor.fetchall()

            if not itens_da_venda:
                st.warning(f"Venda ID {venda_id_para_deletar} não encontrada ou não possui itens.")
                raise Exception("Venda sem itens")

            for item in itens_da_venda:
                cursor.execute(
                    "UPDATE produtos SET quantidade_estoque = quantidade_estoque + %s WHERE id = %s",
                    (item["quantidade_vendida"], item["produto_id"])
                )
            cursor.execute("DELETE FROM vendas WHERE id = %s", (venda_id_para_deletar,)) # ON DELETE CASCADE cuida de itens_venda
        conn.commit()
        st.success(f"Venda ID {venda_id_para_deletar} deletada e estoque dos produtos revertido.")
        get_produtos_do_bd_pg.clear()
        get_caixa_total_do_bd_pg.clear()
        get_estoque_atual_do_bd_pg.clear()
        get_vendas_do_bd_pg.clear()
    except Exception as e:
        conn.rollback()
        if str(e) not in ["Venda sem itens"]:
            st.error(f"Erro ao deletar venda ID {venda_id_para_deletar} (PG): {e}. Transação revertida.")


@st.cache_data(show_spinner="Calculando caixa...")
def get_caixa_total_do_bd_pg(conn) -> float:
    with conn.cursor() as cursor:
        cursor.execute("SELECT SUM(valor_total) FROM vendas")
        resultado = cursor.fetchone()
    return resultado[0] if resultado and resultado[0] is not None else 0.0

@st.cache_data(show_spinner="Verificando estoque...")
def get_estoque_atual_do_bd_pg(conn) -> pd.DataFrame:
    estoque_list = []
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
        cursor.execute("SELECT nome, quantidade_estoque, valor FROM produtos WHERE quantidade_estoque >= 0 ORDER BY nome")
        for row in cursor.fetchall():
            estoque_list.append({"Produto": row["nome"], "Quantidade": row["quantidade_estoque"], "Valor Unitário": f"R${row['valor']:.2f}"})
    return pd.DataFrame(estoque_list)


# --- Interface Streamlit (UI) ---
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

# Conexão Singleton com o BD PostgreSQL
@st.cache_resource # Cache da conexão para não reabrir a cada rerun
def get_db_connection_pg():
    return conectar_bd_pg()

db_connection_pg = get_db_connection_pg()
criar_tabelas_se_nao_existirem_pg(db_connection_pg)


if st.sidebar.button("🔄 Sincronizar Produtos do CSV com o Banco de Dados (PG)"):
    sincronizar_csv_com_bd_pg(db_connection_pg, GITHUB_CSV_URL)
    st.rerun()

with db_connection_pg.cursor() as cursor_check_pg:
    cursor_check_pg.execute("SELECT COUNT(*) FROM produtos")
    if cursor_check_pg.fetchone()[0] == 0:
        st.sidebar.info("O banco de dados de produtos (PG) parece estar vazio. "
                        "Clique em 'Sincronizar Produtos do CSV...' para carregar.")


tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "ℹ️ Produtos e Caixa", "🛒 Registrar Venda", "📊 Vendas Realizadas",
    "📦 Estoque Atual", "⚙️ Gerenciar Produtos (BD)"
])

with tab1:
    st.subheader("Produtos Disponíveis para Venda")
    produtos_bd_tab1 = get_produtos_do_bd_pg(db_connection_pg)
    # ... (lógica de exibição como antes, usando os dados de produtos_bd_tab1) ...
    produtos_em_estoque_vis = [p for p in produtos_bd_tab1 if p["quantidade_estoque"] > 0]
    if produtos_em_estoque_vis:
        df_display_tab1 = pd.DataFrame(produtos_em_estoque_vis)
        df_display_tab1['valor_formatado'] = df_display_tab1['valor'].apply(lambda x: f"R${x:.2f}")
        st.table(df_display_tab1[['nome', 'valor_formatado', 'quantidade_estoque']].rename(
            columns={'nome':'Produto', 'valor_formatado':'Valor Unitário', 'quantidade_estoque':'Em Estoque'}
        ))
    elif not produtos_bd_tab1:
        st.info("Nenhum produto cadastrado no banco de dados (PG).")
    else:
        st.info("Nenhum produto com estoque disponível no momento.")

    st.subheader("💰 Caixa")
    caixa_total_bd = get_caixa_total_do_bd_pg(db_connection_pg)
    st.metric(label="Valor em Caixa", value=f"R${caixa_total_bd:.2f}")


with tab2:
    st.subheader("Registrar Nova Venda")
    produtos_para_venda_bd_tab2 = get_produtos_do_bd_pg(db_connection_pg)
    # ... (lógica do formulário de venda como antes, usando produtos_para_venda_bd_tab2 e registrar_venda_bd_pg) ...
    if not produtos_para_venda_bd_tab2:
        st.warning("Não há produtos cadastrados para registrar uma venda (PG).")
    else:
        with st.form(key='registrar_venda_form_bd_pg'):
            input_produtos_para_venda_dict = {}
            for produto_info in produtos_para_venda_bd_tab2:
                if produto_info["quantidade_estoque"] > 0:
                    quantidade_selecionada = st.number_input(
                        f"{produto_info['nome']} (Estoque: {produto_info['quantidade_estoque']}, "
                        f"Valor: R${produto_info['valor']:.2f})",
                        min_value=0, max_value=produto_info["quantidade_estoque"], step=1,
                        key=f"venda_bd_pg_{produto_info['nome']}"
                    )
                    if quantidade_selecionada > 0:
                        input_produtos_para_venda_dict[produto_info['nome']] = quantidade_selecionada
            submit_venda_bd = st.form_submit_button("Registrar Venda")
            if submit_venda_bd:
                if input_produtos_para_venda_dict:
                    venda_id_registrada, valor_total_registrado = registrar_venda_bd_pg(
                        db_connection_pg, input_produtos_para_venda_dict
                    )
                    if venda_id_registrada:
                        st.success(f"Venda ID {venda_id_registrada} registrada! Valor: R${valor_total_registrado:.2f}")
                        st.rerun()
                else:
                    st.warning("Nenhum produto selecionado ou quantidade inválida.")

with tab3:
    st.subheader("Histórico de Vendas")
    vendas_registradas_bd = get_vendas_do_bd_pg(db_connection_pg)
    # ... (lógica de exibição e deleção de vendas como antes, usando vendas_registradas_bd e deletar_venda_bd_pg) ...
    if vendas_registradas_bd:
        vendas_formatadas_para_display = []
        for venda_row_dict in vendas_registradas_bd:
            horario_venda = venda_row_dict["horario"]
            horario_str = horario_venda.strftime("%d/%m/%Y %H:%M:%S") if isinstance(horario_venda, datetime) else str(horario_venda)

            vendas_formatadas_para_display.append({
                "ID": venda_row_dict["venda_id"],
                "Horário da Venda": horario_str,
                "Itens Vendidos": venda_row_dict["produtos_detalhados"] if venda_row_dict.get("produtos_detalhados") else "N/A",
                "Valor Total (R$)": f"{venda_row_dict['valor_total']:.2f}"
            })
        df_vendas_para_display = pd.DataFrame(vendas_formatadas_para_display)
        st.dataframe(df_vendas_para_display, use_container_width=True)

        if not df_vendas_para_display.empty:
            csv_export_data = df_vendas_para_display.to_csv(index=False, sep=';').encode('utf-8-sig')
            st.download_button(
                label="Baixar Histórico de Vendas como CSV", data=csv_export_data,
                file_name=f"historico_vendas_db_pg_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
            )
        st.subheader("Deletar Venda Registrada")
        ids_vendas_existentes = [v["venda_id"] for v in vendas_registradas_bd]
        if ids_vendas_existentes:
            venda_id_del = st.selectbox("ID da Venda para Deletar", options=ids_vendas_existentes, index=None)
            if st.button("Confirmar Deleção", disabled=(venda_id_del is None)):
                deletar_venda_bd_pg(db_connection_pg, venda_id_del)
                st.rerun()
    else:
        st.info("Nenhuma venda registrada no banco de dados (PG) ainda.")


with tab4:
    st.subheader("Estoque Atual de Produtos")
    df_estoque_atual_bd = get_estoque_atual_do_bd_pg(db_connection_pg)
    # ... (lógica de exibição do estoque como antes) ...
    if not df_estoque_atual_bd.empty:
        st.dataframe(df_estoque_atual_bd, use_container_width=True)
    else:
        st.info("Nenhum produto cadastrado no estoque (PG).")

with tab5:
    st.subheader("Gerenciar Produtos (Persistente no Banco de Dados PG)")
    # ... (lógica de adicionar/deletar produtos como antes, usando adicionar_produto_bd_pg e deletar_produto_bd_pg) ...
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Adicionar Novo Produto ao BD (PG)")
        with st.form(key='add_produto_bd_form_tab5_pg'):
            nome_novo = st.text_input("Nome do Produto")
            valor_novo = st.number_input("Valor Unitário (R$)", min_value=0.01, step=0.01, format="%.2f")
            qtd_nova = st.number_input("Qtd Inicial em Estoque", min_value=0, step=1)
            submit_add = st.form_submit_button("Adicionar Produto ao BD (PG)")
            if submit_add:
                if nome_novo and valor_novo > 0:
                    adicionar_produto_bd_pg(db_connection_pg, nome_novo, valor_novo, qtd_nova)
                    st.rerun()
                else:
                    st.error("Nome e valor (>0) são obrigatórios.")
    with col2:
        st.markdown("#### Deletar Produto Existente do BD (PG)")
        produtos_atuais_del_bd = get_produtos_do_bd_pg(db_connection_pg)
        if produtos_atuais_del_bd:
            nomes_produtos_del = [p["nome"] for p in produtos_atuais_del_bd]
            if nomes_produtos_del:
                produto_del = st.selectbox("Produto para Deletar do BD (PG)", options=nomes_produtos_del, index=None)
                if st.button("Confirmar Deleção do Produto (PG)", disabled=(produto_del is None)):
                    deletar_produto_bd_pg(db_connection_pg, produto_del)
                    st.rerun()
            else:
                st.info("Nenhum produto para deletar (PG).")
        else:
            st.info("Nenhum produto cadastrado (PG).")
