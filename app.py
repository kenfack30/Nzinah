from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.sql import func
from datetime import datetime, timedelta
import csv
import io
from io import BytesIO
import requests 
import smtplib
from email.mime.text import MIMEText
import os 

from pollinations import Pollinations

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm

app = Flask(__name__)
app.config['SECRET_KEY'] = 'une_cle_secrete_tres_difficile_a_deviner'

# ==============================================================
# CONFIGURATION DE LA BASE DE DONNÉES (LOCAL vs RENDER)
# ==============================================================
db_url = os.environ.get('DATABASE_URL', 'sqlite:///avis.db')
# On adapte l'URL pour le pilote psycopg2
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
# psycopg2 est le pilote par défaut pour postgresql://, on n'a pas besoin de le préciser

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ==============================================================
# CONFIGURATION SMS
CLE_API_SMS = "6a4fbe7ae8ebddd063ae97a87cbb28e24bbdfa66d16b696f"
URL_API_SMS = "https://api.smsmobileapi.com/sendsms/"

def envoyer_sms_negatif(numero_telephone, nom_entreprise, total_negatifs):
    if not numero_telephone: return
    try:
        numero_formate = numero_telephone.strip()
        if not numero_formate.startswith('+'):
            if numero_formate.startswith('6'): numero_formate = '+237' + numero_formate
            else: numero_formate = '+' + numero_formate

        message = f"⚠️ Nzinah : Attention {nom_entreprise}, vous venez d'atteindre {total_negatifs} avis négatifs. Connectez-vous vite pour y répondre !"
        payload = {'apikey': CLE_API_SMS, 'recipients': numero_formate, 'message': message}
        response = requests.post(URL_API_SMS, data=payload, timeout=10)
        print(f"SMS API Response: {response.text}")
    except Exception as e:
        print(f"❌ Erreur SMS : {e}")

# ==============================================================
# CONFIGURATION IA (POLLINATIONS)
CLE_API_POLLINATIONS = "sk_ZVxmfVqjO01Tm3Hxq67UcrXFZCQmasOm"

# ==============================================================
# CONFIGURATION SERPKITE
CLE_API_SERPKITE = "skt_live_gxAgWwfsqmMOOx0pHnP7OJlXRcLmBD50"
URL_API_MAPS = "https://api.serpkite.com/v1/maps"
URL_API_REVIEWS = "https://api.serpkite.com/v1/reviews"

# ==============================================================
# CONFIGURATION EMAIL (Pour les relances)
EMAIL_EXPEDITEUR = "ton.email.pro@gmail.com" 
EMAIL_MOT_DE_PASSE = "ton_mot_de_passe_application" 

# --- BASE DE DONNÉES ---
class Utilisateur(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom_entreprise = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    telephone = db.Column(db.String(20), nullable=True)
    mot_de_passe = db.Column(db.String(200), nullable=False)
    
    date_inscription = db.Column(db.DateTime, default=datetime.utcnow)
    plan = db.Column(db.String(20), default='Gratuit')
    date_expiration = db.Column(db.DateTime, nullable=True)
    recherches_google = db.Column(db.Integer, default=0)

    def jours_restants(self):
        if self.plan == 'Premium': return None
        fin_essai = self.date_inscription + timedelta(days=14)
        return max(0, (fin_essai - datetime.utcnow()).days)

    def est_actif(self):
        if self.plan == 'Premium':
            return self.date_expiration and self.date_expiration > datetime.utcnow()
        return self.jours_restants() > 0

class Avis(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client = db.Column(db.String(100), nullable=False)
    plateforme = db.Column(db.String(50), nullable=False)
    note = db.Column(db.Integer, nullable=False)
    texte = db.Column(db.Text, nullable=False)
    reponse = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('utilisateur.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return Utilisateur.query.get(int(user_id))

# --- ROUTES ---
@app.route('/')
def home():
    if current_user.is_authenticated: return redirect(url_for('accueil'))
    return render_template('home.html')

@app.route('/inscription', methods=['GET', 'POST'])
def inscription():
    if current_user.is_authenticated: return redirect(url_for('accueil'))
    if request.method == 'POST':
        email = request.form['email']
        nom = request.form['nom_entreprise']
        telephone = request.form['telephone']
        password = request.form['password']
        
        if Utilisateur.query.filter_by(email=email).first():
            flash("Cet email est déjà utilisé.", "error")
            return redirect(url_for('inscription'))
            
        nouvel_user = Utilisateur(nom_entreprise=nom, email=email, telephone=telephone, mot_de_passe=generate_password_hash(password, method='pbkdf2:sha256'))
        db.session.add(nouvel_user)
        db.session.commit()
        login_user(nouvel_user)
        flash("Bienvenue ! Votre essai de 14 jours a commencé.", "success")
        return redirect(url_for('accueil'))
    return render_template('inscription.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('accueil'))
    if request.method == 'POST':
        user = Utilisateur.query.filter_by(email=request.form['email']).first()
        se_souvenir = 'remember' in request.form
        
        if user and check_password_hash(user.mot_de_passe, request.form['password']):
            login_user(user, remember=se_souvenir)
            return redirect(url_for('accueil'))
        flash("Email ou mot de passe incorrect.", "error")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def accueil():
    if not current_user.est_actif():
        flash("Votre essai est terminé. Choisissez un abonnement.", "error")
        return redirect(url_for('abonnement'))

    if request.method == 'POST':
        if 'repondre' in request.form:
            avis_id = request.form['avis_id']
            texte_reponse = request.form.get('texte_reponse', '').strip()
            if texte_reponse:
                avis_a_modifier = Avis.query.filter_by(id=avis_id, user_id=current_user.id).first()
                if avis_a_modifier:
                    avis_a_modifier.reponse = texte_reponse
                    db.session.commit()
                    flash("Réponse publiée !", "success")
                    
        elif 'generer_ia' in request.form:
            avis_id = request.form['avis_id']
            avis_a_modifier = Avis.query.filter_by(id=avis_id, user_id=current_user.id).first()
            if avis_a_modifier:
                try:
                    client_ia = Pollinations(api_key=CLE_API_POLLINATIONS)
                    prompt = f"Tu es le gérant d'une entreprise. Réponds poliment et professionnellement à cet avis client en français : '{avis_a_modifier.texte}'"
                    reponse_ia = client_ia.generate_text(prompt)
                    avis_a_modifier.reponse = reponse_ia.strip()
                    db.session.commit()
                    flash("L'IA a généré une réponse !", "success")
                except Exception as e:
                    print(f"❌ ERREUR IA : {e}")
                    flash("L'IA a eu un problème. Réessayez.", "error")
                
        elif 'supprimer_avis' in request.form:
            avis_id = request.form['avis_id']
            avis_a_supprimer = Avis.query.filter_by(id=avis_id, user_id=current_user.id).first()
            if avis_a_supprimer:
                db.session.delete(avis_a_supprimer)
                db.session.commit()
                flash("Avis supprimé.", "success")
            
        return redirect(url_for('accueil'))

    tous_les_avis = Avis.query.filter_by(user_id=current_user.id).order_by(Avis.id.desc()).limit(5).all()
    total_avis = Avis.query.filter_by(user_id=current_user.id).count()
    note_moyenne = db.session.query(func.avg(Avis.note)).filter(Avis.user_id == current_user.id).scalar() or 0
    avis_negatifs = Avis.query.filter(Avis.user_id == current_user.id, Avis.note <= 2).count()

    return render_template('index.html', avis=tous_les_avis, total=total_avis, moyenne=round(note_moyenne, 1), negatifs=avis_negatifs)

@app.route('/synchroniser-google', methods=['GET', 'POST'])
@login_required
def synchroniser_google():
    if current_user.plan == 'Gratuit' and current_user.recherches_google >= 10:
        flash("Vous avez utilisé vos 10 crédits gratuits. Passez au plan Premium pour une synchronisation illimitée !", "error")
        return redirect(url_for('abonnement'))

    if request.method == 'POST':
        query = request.form.get('query_entreprise')
        if not query:
            flash("Veuillez entrer le nom de votre établissement.", "error")
            return redirect(url_for('synchroniser_google'))

        try:
            headers = {"Authorization": f"Bearer {CLE_API_SERPKITE}"}
            
            maps_params = {"q": query}
            maps_response = requests.get(URL_API_MAPS, headers=headers, params=maps_params)
            
            if maps_response.status_code != 200:
                flash(f"Erreur de recherche : {maps_response.text}", "error")
                return redirect(url_for('synchroniser_google'))
                
            maps_data = maps_response.json()
            if not maps_data.get('results'):
                flash("Aucun établissement trouvé avec ce nom.", "error")
                return redirect(url_for('synchroniser_google'))
                
            place_id = maps_data['results'][0].get('place_id')
            nom_trouve = maps_data['results'][0].get('title', query)
            
            reviews_params = {"place_id": place_id, "num": 10}
            reviews_response = requests.get(URL_API_REVIEWS, headers=headers, params=reviews_params)
            
            if reviews_response.status_code != 200:
                flash(f"Erreur de récupération des avis : {reviews_response.text}", "error")
                return redirect(url_for('synchroniser_google'))

            reviews_data = reviews_response.json()
            
            avis_importes = 0
            nouveaux_negatifs = 0
            
            for review in reviews_data.get('results', []):
                if review.get('snippet'):
                    avis_existant = Avis.query.filter_by(texte=review['snippet'], user_id=current_user.id).first()
                    if not avis_existant:
                        note = review.get('rating', 0)
                        nouvel_avis = Avis(
                            client=review.get('user', {}).get('name', 'Client Google'),
                            plateforme='Google',
                            note=note,
                            texte=review['snippet'],
                            user_id=current_user.id
                        )
                        db.session.add(nouvel_avis)
                        avis_importes += 1
                        if note <= 2:
                            nouveaux_negatifs += 1
            
            current_user.recherches_google += 1
            db.session.commit()
            
            total_negatifs_apres = Avis.query.filter(Avis.user_id == current_user.id, Avis.note <= 2).count()
            total_negatifs_avant = total_negatifs_apres - nouveaux_negatifs
            
            if (total_negatifs_apres // 5) > (total_negatifs_avant // 5):
                envoyer_sms_negatif(current_user.telephone, current_user.nom_entreprise, total_negatifs_apres)
            
            flash(f"✅ {avis_importes} nouveaux avis importés depuis '{nom_trouve}' !", "success")
            return redirect(url_for('tous_les_avis'))
            
        except Exception as e:
            flash(f"Erreur de connexion : {e}", "error")
            return redirect(url_for('synchroniser_google'))

    return render_template('synchroniser.html')

@app.route('/tous-les-avis')
@login_required
def tous_les_avis():
    if not current_user.est_actif(): return redirect(url_for('abonnement'))
    avis = Avis.query.filter_by(user_id=current_user.id).order_by(Avis.id.desc()).all()
    return render_template('tous_les_avis.html', avis=avis)

@app.route('/importer', methods=['GET', 'POST'])
@login_required
def importer():
    if not current_user.est_actif(): return redirect(url_for('abonnement'))

    if request.method == 'POST':
        fichier = request.files.get('fichier_csv')
        if fichier and fichier.filename.endswith('.csv'):
            stream = io.StringIO(fichier.stream.read().decode("UTF8"), newline=None)
            csv_input = csv.reader(stream)
            next(csv_input, None)
            compteur = 0
            nouveaux_negatifs = 0
            
            for row in csv_input:
                if len(row) >= 4:
                    try:
                        note = int(row[2])
                        nouvel_avis = Avis(client=row[0], plateforme=row[1], note=note, texte=row[3], user_id=current_user.id)
                        db.session.add(nouvel_avis)
                        compteur += 1
                        if note <= 2:
                            nouveaux_negatifs += 1
                    except:
                        pass
            db.session.commit()
            
            total_negatifs_apres = Avis.query.filter(Avis.user_id == current_user.id, Avis.note <= 2).count()
            total_negatifs_avant = total_negatifs_apres - nouveaux_negatifs
            
            if (total_negatifs_apres // 5) > (total_negatifs_avant // 5):
                envoyer_sms_negatif(current_user.telephone, current_user.nom_entreprise, total_negatifs_apres)

            flash(f'{compteur} avis importés !', "success")
            return redirect(url_for('tous_les_avis'))
        else:
            flash('Format invalide. Utilisez un fichier .csv', "error")
    return render_template('importer.html')

@app.route('/parametres', methods=['GET', 'POST'])
@login_required
def parametres():
    if request.method == 'POST':
        if 'modifier_nom' in request.form:
            nouveau_nom = request.form['nom_entreprise']
            if nouveau_nom.strip():
                current_user.nom_entreprise = nouveau_nom
                db.session.commit()
                flash("Nom mis à jour !", "success")
        elif 'modifier_mdp' in request.form:
            if check_password_hash(current_user.mot_de_passe, request.form['ancien_mdp']):
                current_user.mot_de_passe = generate_password_hash(request.form['nouveau_mdp'], method='pbkdf2:sha256')
                db.session.commit()
                flash("Mot de passe modifié !", "success")
            else:
                flash("Ancien mot de passe incorrect.", "error")
        return redirect(url_for('parametres'))
    return render_template('parametres.html')

@app.route('/abonnement')
@login_required
def abonnement():
    return render_template('abonnement.html')

@app.route('/widget')
@login_required
def widget():
    code_html = f'<script src="http://127.0.0.1:5000/widget-api/{current_user.id}"></script>'
    return render_template('widget.html', code_html=code_html)

@app.route('/widget-api/<int:user_id>')
def widget_api(user_id):
    user = Utilisateur.query.get(user_id)
    if not user: return "document.write('<p>Widget introuvable</p>');"
    avis = Avis.query.filter_by(user_id=user.id).all()
    if not avis: return "document.write('<p>Aucun avis</p>');"
    moyenne = sum([a.note for a in avis]) / len(avis)
    etoiles = '⭐' * round(moyenne)
    script_js = f"""
    document.write(`
        <div style="font-family: Arial, sans-serif; background: white; border: 1px solid #e5e7eb; padding: 15px; border-radius: 8px; display: inline-block; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
            <div style="font-size: 20px; margin-bottom: 5px;">{etoiles}</div>
            <div style="font-size: 14px; color: #6b7280;">
                <strong>{moyenne:.1f} / 5</strong> basé sur {len(avis)} avis clients
            </div>
        </div>
    `);
    """
    return script_js, 200, {'Content-Type': 'application/javascript'}

@app.route('/rapport-pdf')
@login_required
def rapport_pdf():
    avis = Avis.query.filter_by(user_id=current_user.id).all()
    total_avis = len(avis)
    moyenne = round(sum([a.note for a in avis]) / total_avis, 1) if total_avis > 0 else 0

    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    p.setFont("Helvetica-Bold", 24)
    p.setFillColorRGB(0.06, 0.72, 0.51)
    p.drawString(2*cm, height - 3*cm, "Nzinah")
    
    p.setFont("Helvetica-Oblique", 14)
    p.setFillColorRGB(0.4, 0.4, 0.4)
    p.drawString(2*cm, height - 4*cm, f"Rapport de reputation - {current_user.nom_entreprise}")
    
    p.setFont("Helvetica", 12)
    p.setFillColorRGB(0, 0, 0)
    p.drawRightString(width - 2*cm, height - 3*cm, f"Date : {datetime.now().strftime('%d/%m/%Y')}")

    p.setFont("Helvetica-Bold", 16)
    p.drawString(2*cm, height - 6*cm, "Vos Statistiques Globales")
    p.setFont("Helvetica", 14)
    p.drawString(2*cm, height - 7*cm, f"Note moyenne : {moyenne} / 5")
    p.drawString(2*cm, height - 8*cm, f"Total des avis collectes : {total_avis}")
    
    avis_negatifs = len([a for a in avis if a.note <= 2])
    p.drawString(2*cm, height - 9*cm, f"Avis negatifs a traiter (<= 2 etoiles) : {avis_negatifs}")

    p.setFont("Helvetica-Bold", 16)
    p.drawString(2*cm, height - 11*cm, "Derniers avis recents")
    
    y_position = height - 12.5*cm
    for a in avis[:5]:
        p.setFont("Helvetica-Bold", 12)
        p.drawString(2*cm, y_position, f"{a.client} - {a.plateforme} ({a.note}/5)")
        p.setFont("Helvetica", 12)
        texte_court = a.texte[:80] + "..." if len(a.texte) > 80 else a.texte
        p.drawString(2*cm, y_position - 0.5*cm, f'"{texte_court}"')
        y_position -= 1.5*cm

    p.showPage()
    p.save()
    buffer.seek(0)
    
    return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=f'Rapport_{current_user.nom_entreprise}.pdf')

# ==============================================================
# PAGE ADMIN SECRÈTE (POUR ACTIVER LES COMPTES MANUELLEMENT)
@app.route('/admin-secret')
@login_required
def admin_secret():
    # ⚠️ SÉCURITÉ : Remplace par TON adresse email
    if current_user.email != "jsongtama@gmail.com": 
        flash("Accès refusé. Vous n'êtes pas administrateur.", "error")
        return redirect(url_for('accueil'))

    clients_gratuits = Utilisateur.query.filter(Utilisateur.plan == 'Gratuit').all()
    clients_premium = Utilisateur.query.filter(Utilisateur.plan == 'Premium').all()
    
    return render_template('admin.html', clients_gratuits=clients_gratuits, clients_premium=clients_premium)

@app.route('/activer-premium/<int:user_id>')
@login_required
def activer_premium(user_id):
    if current_user.email != "jsongtama@gmail.com":
        flash("Accès refusé.", "error")
        return redirect(url_for('accueil'))

    user = Utilisateur.query.get(user_id)
    if user:
        user.plan = 'Premium'
        user.date_expiration = datetime.utcnow() + timedelta(days=30)
        db.session.commit()
        flash(f"Le compte de {user.nom_entreprise} a été passé en Premium pour 30 jours !", "success")
    
    return redirect(url_for('admin_secret'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=False)
