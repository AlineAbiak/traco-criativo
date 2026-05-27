from flask import g, Flask, render_template, request, redirect, session, flash
from flask_socketio import SocketIO
import sqlite3
from functools import wraps

app = Flask(__name__)
app.secret_key = "traco_criativo_secret"
socketio = SocketIO(app)

DB_NAME = "database.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS produtos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        preco REAL NOT NULL,
        quantidade INTEGER DEFAULT 0
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        email TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS vendas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER,
        valor_total REAL,
        data_venda TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS itens_venda (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        venda_id INTEGER,
        produto_id INTEGER,
        qtd INTEGER,
        preco_unitario REAL,
        subtotal REAL
    )
    """)

    conn.commit()
    conn.close()



def conectar():
    if "db" not in g:
        g.db = sqlite3.connect(DB_NAME, timeout=30)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def fechar_conexao(exception):
    db = g.pop("db", None)
    if db:
        db.close()




def login_obrigatorio(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "usuario" not in session:
            return redirect("/")
        return f(*args, **kwargs)
    return wrapper



def calcular_cards():
    conn = conectar()
    cursor = conn.cursor()

  
    cursor.execute("SELECT IFNULL(SUM(subtotal),0) AS total FROM itens_venda")
    faturamento = cursor.fetchone()["total"]

  
    cursor.execute("SELECT COUNT(DISTINCT id) AS total FROM vendas")
    total_vendas = cursor.fetchone()["total"]

    cursor.execute("""
        SELECT p.nome, SUM(iv.qtd) as total
        FROM itens_venda iv
        JOIN produtos p ON p.id = iv.produto_id
        GROUP BY p.nome
        ORDER BY total DESC
        LIMIT 1
    """)
    mais_vendido = cursor.fetchone()
    produto_top = mais_vendido["nome"] if mais_vendido else "Nenhum"

    return faturamento, total_vendas, produto_top

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form["usuario"] == "admin" and request.form["senha"] == "admin":
            session["usuario"] = "admin"
            return redirect("/dashboard")

        flash("Login inválido")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")



@app.route("/dashboard")
@login_obrigatorio
def dashboard():
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT p.nome, SUM(iv.qtd) as total
        FROM itens_venda iv
        JOIN produtos p ON p.id = iv.produto_id
        GROUP BY p.nome
    """)

    dados = cursor.fetchall()

    return render_template(
        "dashboard.html",
        nomes=[d["nome"] for d in dados],
        totais=[d["total"] for d in dados]
    )



@app.route("/produtos")
@login_obrigatorio
def produtos():
    conn = conectar()
    cursor = conn.cursor()

    
    cursor.execute("SELECT id, nome, preco, IFNULL(quantidade,0) AS quantidade FROM produtos")
    produtos = cursor.fetchall()

   
    cursor.execute("SELECT IFNULL(SUM(subtotal),0) AS faturamento FROM itens_venda")
    faturamento = cursor.fetchone()["faturamento"]

    cursor.execute("SELECT COUNT(DISTINCT id) AS total_vendas FROM vendas")
    total_vendas = cursor.fetchone()["total_vendas"]

    cursor.execute("""
        SELECT p.nome, SUM(iv.qtd) as total
        FROM itens_venda iv
        JOIN produtos p ON p.id = iv.produto_id
        GROUP BY p.nome
        ORDER BY total DESC
        LIMIT 1
    """)
    mais_vendido = cursor.fetchone()
    produto_top = mais_vendido["nome"] if mais_vendido else "Nenhum"

    return render_template(
        "produtos.html",
        produtos=produtos,
        faturamento=faturamento,
        total_vendas=total_vendas,
        produto_top=produto_top
    )



@app.route("/cadastrar", methods=["GET", "POST"])
@login_obrigatorio
def cadastrar():

    if request.method == "POST":

        nome = request.form["nome"]
        preco = float(request.form["preco"])
        quantidade = int(request.form.get("quantidade", 0))

        conn = conectar()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO produtos (nome, preco, quantidade)
            VALUES (?, ?, ?)
        """, (nome, preco, quantidade))

        conn.commit()

        socketio.emit("atualizar_grafico")

        flash("Produto cadastrado com estoque!")

        return redirect("/produtos")

    return render_template("cadastrar.html")



@app.route("/vendas", methods=["GET", "POST"])
@login_obrigatorio
def vendas():

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 
            id,
            nome,
            preco,
            IFNULL(quantidade, 0) AS quantidade
        FROM produtos
    """)

    produtos = cursor.fetchall()

    cursor.execute("SELECT * FROM clientes")
    clientes = cursor.fetchall()

    if request.method == "POST":

        cliente_id = request.form["cliente_id"]

        produtos_ids = request.form.getlist("produto_id[]")
        quantidades = request.form.getlist("qtd[]")

        valor_total = 0
        itens = []

        for produto_id, qtd in zip(produtos_ids, quantidades):

            qtd = int(qtd)

            cursor.execute("""
                SELECT *
                FROM produtos
                WHERE id = ?
            """, (produto_id,))

            produto = cursor.fetchone()

            if not produto:

                flash("Produto inválido!")
                return redirect("/vendas")

            estoque = produto["quantidade"] or 0

           
            if estoque < qtd:

                flash(
                    f"Estoque insuficiente para {produto['nome']}"
                )

                return redirect("/vendas")

            subtotal = produto["preco"] * qtd

            valor_total += subtotal

            itens.append((produto, qtd, subtotal))

        
        cursor.execute("""
            INSERT INTO vendas (
                cliente_id,
                valor_total
            )
            VALUES (?, ?)
        """, (
            cliente_id,
            valor_total
        ))

        venda_id = cursor.lastrowid

      
        for produto, qtd, subtotal in itens:

           
            cursor.execute("""
                UPDATE produtos
                SET quantidade = quantidade - ?
                WHERE id = ?
            """, (
                qtd,
                produto["id"]
            ))

            cursor.execute("""
                INSERT INTO itens_venda (
                    venda_id,
                    produto_id,
                    qtd,
                    preco_unitario,
                    subtotal
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                venda_id,
                produto["id"],
                qtd,
                produto["preco"],
                subtotal
            ))

        conn.commit()

        socketio.emit("atualizar_grafico")

        flash("Venda registrada com sucesso!")

        return redirect("/vendas")

    return render_template(
        "vendas.html",
        produtos=produtos,
        clientes=clientes
    )



@app.route("/clientes", methods=["GET", "POST"])
@login_obrigatorio
def clientes():

    conn = conectar()
    cursor = conn.cursor()

    if request.method == "POST":

        nome = request.form["nome"]
        email = request.form["email"]

        try:

            cursor.execute("""
                INSERT INTO clientes (
                    nome,
                    email
                )
                VALUES (?, ?)
            """, (
                nome,
                email
            ))

            conn.commit()

            socketio.emit("atualizar_grafico")

            flash("Cliente cadastrado com sucesso!")

        except Exception as e:

            conn.rollback()

            flash(f"Erro ao cadastrar cliente: {str(e)}")

    cursor.execute("""
        SELECT *
        FROM clientes
        ORDER BY id DESC
    """)

    clientes = cursor.fetchall()

    return render_template(
        "clientes.html",
        clientes=clientes
    )

@app.route("/excluir_produto/<int:id>")
@login_obrigatorio
def excluir_produto(id):

    conn = conectar()
    cursor = conn.cursor()

    try:

        
        cursor.execute("""
            SELECT DISTINCT venda_id
            FROM itens_venda
            WHERE produto_id = ?
        """, (id,))

        vendas_ids = [v["venda_id"] for v in cursor.fetchall()]

       
        cursor.execute("""
            DELETE FROM itens_venda
            WHERE produto_id = ?
        """, (id,))

     
        for venda_id in vendas_ids:

            cursor.execute("""
                SELECT COUNT(*)
                FROM itens_venda
                WHERE venda_id = ?
            """, (venda_id,))

            total_itens = cursor.fetchone()[0]

            if total_itens == 0:

                cursor.execute("""
                    DELETE FROM vendas
                    WHERE id = ?
                """, (venda_id,))

      
        cursor.execute("""
            DELETE FROM produtos
            WHERE id = ?
        """, (id,))

        conn.commit()

        socketio.emit("atualizar_grafico")

        flash("Produto excluído com sucesso!")

    except Exception as e:

        conn.rollback()
        flash(f"Erro ao excluir produto: {str(e)}")

    return redirect("/produtos")


@app.route("/cliente/<int:id>")
@login_obrigatorio
def cliente_detalhe(id):
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM clientes WHERE id = ?", (id,))
    cliente = cursor.fetchone()

    if not cliente:
        flash("Cliente não encontrado")
        return redirect("/clientes")

    cursor.execute("""
        SELECT COUNT(DISTINCT v.id)
        FROM vendas v
        WHERE v.cliente_id = ?
    """, (id,))
    total_compras = cursor.fetchone()[0] or 0

    cursor.execute("""
        SELECT SUM(iv.subtotal)
        FROM vendas v
        JOIN itens_venda iv ON iv.venda_id = v.id
        WHERE v.cliente_id = ?
    """, (id,))
    total_gasto = cursor.fetchone()[0] or 0

    cursor.execute("""
        SELECT v.id AS venda_id,
               v.data_venda,
               v.valor_total,
               p.nome AS produto_nome,
               iv.qtd,
               iv.preco_unitario,
               iv.subtotal
        FROM vendas v
        JOIN itens_venda iv ON iv.venda_id = v.id
        JOIN produtos p ON p.id = iv.produto_id
        WHERE v.cliente_id = ?
        ORDER BY v.id DESC
    """, (id,))

    vendas = cursor.fetchall()

    return render_template(
        "cliente_detalhe.html",
        cliente=cliente,
        vendas=vendas,
        total_compras=total_compras,
        total_gasto=total_gasto
    )

@app.route("/excluir_cliente/<int:id>")
@login_obrigatorio
def excluir_cliente(id):

    conn = conectar()
    cursor = conn.cursor()

    try:

        cursor.execute("""
            DELETE FROM itens_venda
            WHERE venda_id IN (
                SELECT id FROM vendas
                WHERE cliente_id = ?
            )
        """, (id,))

        cursor.execute("""
            DELETE FROM vendas
            WHERE cliente_id = ?
        """, (id,))

        cursor.execute("""
            DELETE FROM clientes
            WHERE id = ?
        """, (id,))

        conn.commit()

        socketio.emit("atualizar_grafico")

        flash("Cliente excluído com sucesso!")

    except Exception as e:

        conn.rollback()
        flash(f"Erro: {str(e)}")

    return redirect("/clientes")


if __name__ == "__main__":
    init_db()
    socketio.run(app, port=80, debug=True)