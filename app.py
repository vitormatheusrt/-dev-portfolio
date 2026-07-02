import os
import sqlite3
import calendar
import holidays
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from collections import defaultdict

app = Flask(__name__)

# Função inteligente para pular finais de semana e feriados
def obter_n_dia_util(ano, mes, n_dia_util):
    # Carrega os feriados do Brasil para o ano específico
    feriados_br = holidays.Brazil(years=ano)
    dias_uteis = 0
    dia = 1
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    
    while dia <= ultimo_dia:
        data_atual = date(ano, mes, dia)
        # weekday() vai de 0 (Segunda) até 6 (Domingo). Então < 5 é dia de semana.
        # Também verifica se a data não está na lista de feriados nacionais.
        if data_atual.weekday() < 5 and data_atual not in feriados_br:
            dias_uteis += 1
            if dias_uteis == n_dia_util:
                return data_atual
        dia += 1
    
    return date(ano, mes, ultimo_dia) # Garantia anti-falhas

# Configurações de Sessão Livres para Teste Local
app.secret_key = "chave_totalmente_aleatoria_e_segura_para_faculdade"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)

BANCO_DADOS = "financas.db"

def conectar():
    conn = sqlite3.connect(BANCO_DADOS)
    conn.row_factory = sqlite3.Row
    return conn

def inicializar_banco():
    conn = conectar()
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            usuario TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL
        )
    ''')
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS transacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            descricao TEXT NOT NULL,
            valor REAL NOT NULL,
            tipo TEXT NOT NULL,
            categoria TEXT,
            data_vencimento TEXT,
            pago INTEGER DEFAULT 0,
            recorrente INTEGER DEFAULT 0,
            frequencia TEXT
        )
    ''')
        
    conn.commit()
    conn.close()

inicializar_banco()

# ==========================================
# ROTAS DE AUTENTICAÇÃO
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_name = request.form.get('log_usuario')
        senha = request.form.get('log_senha')
        
        if not login_name or not senha:
            flash("Preencha todos os campos para entrar.")
            return redirect(url_for('login'))
            
        login_name = login_name.strip().lower()
        
        conn = conectar()
        usuario = conn.execute('SELECT * FROM usuarios WHERE usuario = ?', (login_name,)).fetchone()
        conn.close()
        
        if usuario and check_password_hash(usuario['senha'], senha):
            session['usuario_id'] = usuario['id']
            session['usuario_nome'] = usuario['nome']
            return redirect(url_for('index'))
        else:
            flash("Usuário ou senha incorretos.")
            
    return render_template('login.html')

@app.route('/cadastro', methods=['POST'])
def cadastro():
    nome_exibicao = request.form.get('cad_nome')
    login_name = request.form.get('cad_usuario')
    senha = request.form.get('cad_senha')
    
    if not nome_exibicao or not login_name or not senha:
        flash("Por favor, preencha todos os campos do cadastro.")
        return redirect(url_for('login'))
        
    nome_exibicao = nome_exibicao.strip()
    login_name = login_name.strip().lower()
    
    if " " in login_name:
        flash("O Nome de Login não pode conter espaços.")
        return redirect(url_for('login'))
        
    if len(senha) < 6:
        flash("A senha precisa ter pelo menos 6 caracteres.")
        return redirect(url_for('login'))
        
    senha_hash = generate_password_hash(senha)
    
    conn = conectar()
    try:
        conn.execute('INSERT INTO usuarios (nome, usuario, senha) VALUES (?, ?, ?)', (nome_exibicao, login_name, senha_hash))
        conn.commit()
        
        usuario = conn.execute('SELECT * FROM usuarios WHERE usuario = ?', (login_name,)).fetchone()
        session['usuario_id'] = usuario['id']
        session['usuario_nome'] = usuario['nome']
        
        conn.close()
        return redirect(url_for('index'))
    except sqlite3.IntegrityError:
        conn.close()
        flash("Este Nome de Login já está em uso. Escolha outro.")
        return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ==========================================
# ROTAS DO SISTEMA
# ==========================================
@app.route('/')
def index():
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
        
    usuario_id = session['usuario_id']
    aba_ativa = request.args.get('aba', 'dashboard')
    
    hoje = datetime.today().strftime("%Y-%m-%d")
    mes_atual = datetime.today().strftime("%Y-%m")
    
    conn = conectar()
    transacoes = conn.execute('SELECT * FROM transacoes WHERE usuario_id = ? ORDER BY data_vencimento DESC', (usuario_id,)).fetchall()
    conn.close()
    
    saldo_real = 0.0
    contas_a_pagar = 0.0
    
    # --- MÁGICA DO EXTRATO FUTURO ---
    # Cria um dicionário que agrupa tudo automaticamente
    extrato_mensal = defaultdict(lambda: {'receitas': 0.0, 'despesas': 0.0, 'saldo_mes': 0.0, 'qtd': 0})
    
    for t in transacoes:
        # 1. Cálculos antigos do Dashboard (MODIFICADO: Apenas para mês atual ou anteriores)
        if t['data_vencimento'][:7] <= mes_atual:
            if t['pago'] == 1:
                if t['tipo'] == 'receita': saldo_real += t['valor']
                else: saldo_real -= t['valor']
            else:
                if t['tipo'] == 'despesa': contas_a_pagar += t['valor']
            
        # 2. Agrupando as contas por Mês/Ano (Ex: "2026-06", "2026-07")
        mes_ano = t['data_vencimento'][:7]
        if t['tipo'] == 'receita':
            extrato_mensal[mes_ano]['receitas'] += t['valor']
            extrato_mensal[mes_ano]['saldo_mes'] += t['valor']
        else:
            extrato_mensal[mes_ano]['despesas'] += t['valor']
            extrato_mensal[mes_ano]['saldo_mes'] -= t['valor']
            
        extrato_mensal[mes_ano]['qtd'] += 1
            
    # MODIFICADO: Atividades Recentes Inteligente (Não repete itens de um mesmo lote e ignora o futuro)
    ultimas_transacoes = []
    vistos = set()
    
    # AQUI ESTÁ A CORREÇÃO: Filtra para pegar apenas as coisas do mês atual ou atrasadas
    transacoes_filtradas = [t for t in transacoes if t['data_vencimento'][:7] <= mes_atual]
    
    # Ordena temporariamente pelos últimos que você cadastrou no banco (dentro do filtro)
    transacoes_recentes = sorted(transacoes_filtradas, key=lambda x: x['id'], reverse=True)
    
    for t in transacoes_recentes:
        nome_base = t['descricao'].split(' (')[0]
        chave = (nome_base, t['valor'])
        
        if chave not in vistos:
            vistos.add(chave)
            
            lote = [x for x in transacoes if x['descricao'].split(' (')[0] == nome_base and x['valor'] == t['valor']]
            if lote:
                primeira_parcela = min(lote, key=lambda x: x['id'])
                ultimas_transacoes.append(primeira_parcela)
                
        if len(ultimas_transacoes) >= 3: # Mantido o seu limite de 3
            break
    
    # Ordena o extrato de forma cronológica (Janeiro, Fevereiro, Março...)
    extrato_ordenado = dict(sorted(extrato_mensal.items()))
    
    # --- ALTERAÇÃO SOLICITADA: LÓGICA DE SALDO ACUMULADO ---
    saldo_acumulado = 0.0
    for m in extrato_ordenado:
        # Pega o saldo isolado deste mês e soma com o acumulado que veio do passado
        saldo_acumulado += extrato_ordenado[m]['saldo_mes']
        # Substitui o saldo do mês pelo novo valor acumulado real
        extrato_ordenado[m]['saldo_mes'] = saldo_acumulado
            
    return render_template('index.html', 
                           transacoes=transacoes, 
                           ultimas_transacoes=ultimas_transacoes,
                           saldo=saldo_real, 
                           contas_a_pagar=contas_a_pagar,
                           aba_ativa=aba_ativa,
                           hoje=hoje,
                           mes_atual=mes_atual,
                           extrato_ordenado=extrato_ordenado)

@app.route('/adicionar', methods=['POST'])
def adicionar():
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
        
    tipo = request.form.get('tipo')
    categoria = request.form.get('categoria')
    descricao = request.form.get('descricao')
    valor = float(request.form.get('valor', 0))
    data_vencimento_str = request.form.get('data_vencimento')
    
    # CORREÇÃO AQUI: Mudámos a variável para 'pago' para combinar com o banco de dados
    pago = request.form.get('pago', '0') 
    
    eh_parcelado = request.form.get('eh_parcelado') in ['1', 'on']
    qtd_parcelas = request.form.get('qtd_parcelas')
    if qtd_parcelas:
        qtd_parcelas = int(qtd_parcelas)
        
    tipo_repeticao = request.form.get('tipo_repeticao', 'mensal')
    id_frequencia_base = int(datetime.now().timestamp())
    
    conn = conectar()
    
    # Bloco que processa as Receitas
    if tipo == 'receita':
        modelo_salario = request.form.get('modelo_salario', 'fixo_mensal')
        
        if modelo_salario == 'fixo_mensal' or categoria != 'Salário/Renda':
            if eh_parcelado and qtd_parcelas:
                data_original = datetime.strptime(data_vencimento_str, '%Y-%m-%d')
                for i in range(qtd_parcelas):
                    ano_p = data_original.year + (data_original.month + i - 1) // 12
                    mes_p = (data_original.month + i - 1) % 12 + 1
                    
                    if 'dia_util' in tipo_repeticao:
                        n_util = data_original.day
                        data_vencimento_p = obter_n_dia_util(ano_p, mes_p, n_util)
                    else:
                        dia_p = min(data_original.day, calendar.monthrange(ano_p, mes_p)[1])
                        data_vencimento_p = date(ano_p, mes_p, dia_p)
                        
                    # CORREÇÃO: Apenas a primeira parcela recebe o status do formulário, as outras ficam como não pago ('0')
                    pago_atual = pago if i == 0 else '0'
                        
                    conn.execute('''
                        INSERT INTO transacoes (usuario_id, tipo, categoria, descricao, valor, data_vencimento, pago, frequencia)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (session['usuario_id'], tipo, categoria, f"{descricao} ({i+1}/{qtd_parcelas})", valor, data_vencimento_p.strftime('%Y-%m-%d'), pago_atual, f"parc_{id_frequencia_base}"))
            else:
                conn.execute('''
                    INSERT INTO transacoes (usuario_id, tipo, categoria, descricao, valor, data_vencimento, pago)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (session['usuario_id'], tipo, categoria, descricao, valor, data_vencimento_str, pago))

        # Se o utilizador escolheu o modelo de Salário Semanal
        elif modelo_salario == 'semanal':
            frequencia_semanal = request.form.get('frequencia_semanal', 'todas')
            dia_da_semana_alvo = int(request.form.get('dia_da_semana', 0))
            
            meses_a_gerar = 1
            if eh_parcelado and qtd_parcelas:
                meses_a_gerar = qtd_parcelas
                
            data_base = datetime.strptime(data_vencimento_str, '%Y-%m-%d')
            
            for m in range(meses_a_gerar):
                ano_alvo = data_base.year + (data_base.month + m - 1) // 12
                mes_alvo = (data_base.month + m - 1) % 12 + 1
                
                datas_do_mes = []
                num_dias_mes = calendar.monthrange(ano_alvo, mes_alvo)[1]
                
                for dia in range(1, num_dias_mes + 1):
                    dt = date(ano_alvo, mes_alvo, dia)
                    if dt.weekday() == dia_da_semana_alvo:
                        datas_do_mes.append(dt)
                
                if frequencia_semanal == '2' and len(datas_do_mes) >= 2:
                    datas_do_mes = [datas_do_mes[0], datas_do_mes[2] if len(datas_do_mes) > 2 else datas_do_mes[-1]]
                elif frequencia_semanal == '3' and len(datas_do_mes) >= 3:
                    datas_do_mes = datas_do_mes[:3]
                
                # CORREÇÃO: Apenas o primeiro mês gerado (m == 0) recebe o status do formulário
                pago_atual = pago if m == 0 else '0'
                
                for idx, data_pagamento in enumerate(datas_do_mes):
                    if meses_a_gerar == 1:
                        desc_semana = f"{descricao} - Sem. {idx+1}"
                    else:
                        desc_semana = f"{descricao} - Mês {m+1} Sem. {idx+1}"
                        
                    conn.execute('''
                        INSERT INTO transacoes (usuario_id, tipo, categoria, descricao, valor, data_vencimento, pago, frequencia)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (session['usuario_id'], tipo, categoria, desc_semana, valor, data_pagamento.strftime('%Y-%m-%d'), pago_atual, f"semanal_{id_frequencia_base}"))

    # Bloco que processa Despesas normais
    else:
        if eh_parcelado and qtd_parcelas:
            data_original = datetime.strptime(data_vencimento_str, '%Y-%m-%d')
            for i in range(qtd_parcelas):
                ano_p = data_original.year + (data_original.month + i - 1) // 12
                mes_p = (data_original.month + i - 1) % 12 + 1
                
                if 'dia_util' in tipo_repeticao:
                    n_util = data_original.day
                    data_vencimento_p = obter_n_dia_util(ano_p, mes_p, n_util)
                else:
                    dia_p = min(data_original.day, calendar.monthrange(ano_p, mes_p)[1])
                    data_vencimento_p = date(ano_p, mes_p, dia_p)
                    
                # CORREÇÃO: Apenas a primeira parcela recebe o status do formulário
                pago_atual = pago if i == 0 else '0'
                    
                conn.execute('''
                    INSERT INTO transacoes (usuario_id, tipo, categoria, descricao, valor, data_vencimento, pago, frequencia)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (session['usuario_id'], tipo, categoria, f"{descricao} ({i+1}/{qtd_parcelas})", valor, data_vencimento_p.strftime('%Y-%m-%d'), pago_atual, f"parc_{id_frequencia_base}"))
        else:
            conn.execute('''
                INSERT INTO transacoes (usuario_id, tipo, categoria, descricao, valor, data_vencimento, pago)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (session['usuario_id'], tipo, categoria, descricao, valor, data_vencimento_str, pago))

    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/excluir_serie/<int:id>')
def excluir_serie(id):
    if 'usuario_id' not in session: return redirect(url_for('login'))
    
    conn = conectar()
    
    # Descobre a qual grupo e data esta transação pertence
    t = conn.execute('SELECT frequencia, data_vencimento FROM transacoes WHERE id = ? AND usuario_id = ?', (id, session['usuario_id'])).fetchone()
    
    if t and t['frequencia']:
        # MÁGICA: Apaga esta parcela e TODAS AS FUTURAS. Assim não perde o histórico dos meses em que já pagou!
        conn.execute('''
            DELETE FROM transacoes 
            WHERE frequencia = ? AND usuario_id = ? AND data_vencimento >= ?
        ''', (t['frequencia'], session['usuario_id'], t['data_vencimento']))
    else:
        # Se for uma conta única, apaga só ela
        conn.execute('DELETE FROM transacoes WHERE id = ? AND usuario_id = ?', (id, session['usuario_id']))
        
    conn.commit()
    conn.close()
    
    return redirect(request.referrer or url_for('index'))

@app.route('/editar_lancamento', methods=['POST'])
def editar_lancamento():
    if 'usuario_id' not in session: 
        return redirect(url_for('login'))

    id_lancamento = request.form.get('id_lancamento')
    nova_descricao = request.form.get('descricao')
    novo_valor = float(request.form.get('valor'))
    tipo_edicao = request.form.get('tipo_edicao')

    # CORREÇÃO: Usar a sua função de conexão exata
    conn = conectar() 

    if tipo_edicao == 'somente_esta':
        # Atualiza apenas o boleto/registro selecionado
        conn.execute("""
            UPDATE transacoes 
            SET descricao = ?, valor = ? 
            WHERE id = ? AND usuario_id = ?
        """, (nova_descricao, novo_valor, id_lancamento, session['usuario_id']))
        
    elif tipo_edicao == 'futuras':
        # 1. Pegamos os dados atuais usando a sua forma exata de buscar (fetchone)
        atual = conn.execute("SELECT descricao, data_vencimento, frequencia FROM transacoes WHERE id = ? AND usuario_id = ?", (id_lancamento, session['usuario_id'])).fetchone()
        
        if atual:
            # Como você usa sqlite3.Row, podemos pegar direto pelo nome da coluna!
            descricao_antiga = atual['descricao']
            data_atual = atual['data_vencimento']
            frequencia_id = atual['frequencia']
            
            if frequencia_id:
                # Se tiver ID de grupo, atualiza todas do grupo dali para frente
                conn.execute("""
                    UPDATE transacoes 
                    SET descricao = ?, valor = ? 
                    WHERE frequencia = ? AND data_vencimento >= ? AND usuario_id = ?
                """, (nova_descricao, novo_valor, frequencia_id, data_atual, session['usuario_id']))
            else:
                # Caso não tenha ID de grupo, atualiza as que tiverem o mesmo nome
                conn.execute("""
                    UPDATE transacoes 
                    SET descricao = ?, valor = ? 
                    WHERE descricao = ? AND data_vencimento >= ? AND usuario_id = ?
                """, (nova_descricao, novo_valor, descricao_antiga, data_atual, session['usuario_id']))

    conn.commit()
    conn.close()

    # Recarrega a página com tudo atualizado
    return redirect(request.referrer or url_for('index'))

@app.route('/alternar_status/<int:id>')
def alternar_status(id):
    if 'usuario_id' not in session: return redirect(url_for('login'))
    
    conn = conectar()
    transacao = conn.execute('SELECT pago FROM transacoes WHERE id = ? AND usuario_id = ?', (id, session['usuario_id'])).fetchone()
    
    if transacao:
        novo_status = 0 if transacao['pago'] == 1 else 1
        conn.execute('UPDATE transacoes SET pago = ? WHERE id = ?', (novo_status, id))
        conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('index', aba='extrato'))

@app.route('/remover/<int:id>')
def remover(id):
    if 'usuario_id' not in session: return redirect(url_for('login'))
    conn = conectar()
    conn.execute('DELETE FROM transacoes WHERE id = ? AND usuario_id = ?', (id, session['usuario_id']))
    conn.commit()
    conn.close()
    return redirect(url_for('index', aba='extrato'))

@app.route('/detalhes/<mes_ano>')
def detalhes(mes_ano):
    if 'usuario_id' not in session: 
        return redirect(url_for('login'))
        
    conn = conectar()
    
    # O comando LIKE ? com o % no final busca tudo que começa com "2026-07", ou seja, todos os dias daquele mês
    transacoes_mes = conn.execute('''
        SELECT * FROM transacoes 
        WHERE usuario_id = ? AND data_vencimento LIKE ? 
        ORDER BY data_vencimento ASC
    ''', (session['usuario_id'], f"{mes_ano}%")).fetchall()
    conn.close()
    
    # Calcula o resumo rápido só desse mês
    entradas = sum(t['valor'] for t in transacoes_mes if t['tipo'] == 'receita')
    saidas = sum(t['valor'] for t in transacoes_mes if t['tipo'] == 'despesa')
    saldo_final = entradas - saidas
    
    return render_template('detalhes.html', 
                           transacoes=transacoes_mes, 
                           mes_ano=mes_ano,
                           entradas=entradas,
                           saidas=saidas,
                           saldo_final=saldo_final)

@app.route('/excluir/<int:id>', methods=['GET', 'POST'])
def excluir(id):
    if 'usuario_id' not in session: 
        return redirect(url_for('login'))
        
    conn = conectar()
    
    # O "AND usuario_id = ?" garante que o usuário só apague as contas dele
    conn.execute('DELETE FROM transacoes WHERE id = ? AND usuario_id = ?', (id, session['usuario_id']))
    conn.commit()
    conn.close()
    
    # Redireciona magicamente de volta para a mesma página que você estava
    return redirect(request.referrer or url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5000)