using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using Waher.Networking;
using Waher.Networking.Sniffers;

namespace Neuro.Networking.Anki
{
    /// <summary>HTTP client for the Python fastanki bridge. AnkiWeb credentials stay in the bridge.</summary>
    public sealed class AnkiClient : CommunicationLayer, IAnkiClient
    {
        private static readonly JsonSerializerOptions json = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
            PropertyNameCaseInsensitive = true,
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
        };
        private readonly HttpClient http;
        private readonly bool ownsHttp;
        private readonly string token;
        private int disposed;

        /// <summary>Use HTTPS, or HTTP on loopback for local development. An injected HttpClient is caller-owned.</summary>
        public AnkiClient(string BaseUrl, string Token, HttpClient? HttpClient = null, params ISniffer[] Sniffers)
            : base(false, Sniffers)
        {
            if (!Uri.TryCreate(BaseUrl, UriKind.Absolute, out Uri? Address) ||
                (Address.Scheme != Uri.UriSchemeHttps && !(Address.Scheme == Uri.UriSchemeHttp && Address.IsLoopback)) ||
                Address.UserInfo.Length != 0 || Address.Query.Length != 0 || Address.Fragment.Length != 0)
                throw new ArgumentException("An HTTPS base URL, or loopback HTTP URL, without credentials/query/fragment is required.", nameof(BaseUrl));
            if (string.IsNullOrWhiteSpace(Token) || Token.Any(char.IsWhiteSpace) || Token.Any(char.IsControl))
                throw new ArgumentException("A bridge token without whitespace or control characters is required.", nameof(Token));
            this.BaseUri = new Uri(Address.AbsoluteUri.TrimEnd('/') + "/");
            this.token = Token;
            this.ownsHttp = HttpClient == null;
            this.http = HttpClient ?? new HttpClient(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = TimeSpan.FromMinutes(10) };
        }

        public Uri BaseUri { get; }
        public bool Disposed => Volatile.Read(ref this.disposed) != 0;

        public Task<List<AnkiDeck>> GetDecksAsync(CancellationToken CancellationToken = default) =>
            this.SendAsync<List<AnkiDeck>>(HttpMethod.Get, "v1/decks", null, CancellationToken);

        public Task<Page<AnkiNote>> GetNotesAsync(long AfterId = 0, int Limit = 100, string? Deck = null,
            string? Tag = null, CancellationToken CancellationToken = default) =>
            this.SendAsync<Page<AnkiNote>>(HttpMethod.Get, "v1/notes" + Query(AfterId, Limit, "deck", Deck, "tag", Tag), null, CancellationToken);

        public Task<AnkiNote> GetNoteAsync(long NoteId, CancellationToken CancellationToken = default) =>
            this.SendAsync<AnkiNote>(HttpMethod.Get, NotePath(NoteId), null, CancellationToken);

        public Task<AnkiNote> CreateNoteAsync(CreateNote Request, CancellationToken CancellationToken = default) =>
            this.SendAsync<AnkiNote>(HttpMethod.Post, "v1/notes", Request ?? throw new ArgumentNullException(nameof(Request)), CancellationToken);

        public Task<AnkiNote> UpdateNoteAsync(long NoteId, UpdateNote Request, CancellationToken CancellationToken = default) =>
            this.SendAsync<AnkiNote>(new HttpMethod("PATCH"), NotePath(NoteId), Request ?? throw new ArgumentNullException(nameof(Request)), CancellationToken);

        public async Task DeleteNoteAsync(long NoteId, CancellationToken CancellationToken = default) =>
            await this.SendAsync<object>(HttpMethod.Delete, NotePath(NoteId), null, CancellationToken).ConfigureAwait(false);

        public Task<Page<AnkiCard>> GetCardsAsync(long AfterId = 0, int Limit = 100, long? NoteId = null,
            string? Deck = null, CancellationToken CancellationToken = default) =>
            this.SendAsync<Page<AnkiCard>>(HttpMethod.Get, "v1/cards" + Query(AfterId, Limit, "note_id", OptionalId(NoteId), "deck", Deck), null, CancellationToken);

        public Task<Page<AnkiReview>> GetReviewsAsync(long AfterId = 0, int Limit = 100, long? CardId = null,
            CancellationToken CancellationToken = default) =>
            this.SendAsync<Page<AnkiReview>>(HttpMethod.Get, "v1/reviews" + Query(AfterId, Limit, "card_id", OptionalId(CardId)), null, CancellationToken);

        public Task<SyncResult> SyncAsync(CancellationToken CancellationToken = default) =>
            this.SendAsync<SyncResult>(HttpMethod.Post, "v1/sync", new { mode = "incremental" }, CancellationToken);

        public Task<SyncResult> DownloadCollectionAsync(CancellationToken CancellationToken = default) =>
            this.SendAsync<SyncResult>(HttpMethod.Post, "v1/sync", new { mode = "download" }, CancellationToken);

        private async Task<T> SendAsync<T>(HttpMethod Method, string Resource, object? Body, CancellationToken CancellationToken)
        {
            if (this.Disposed) throw new ObjectDisposedException(nameof(AnkiClient));
            using var Request = new HttpRequestMessage(Method, new Uri(this.BaseUri, Resource));
            Request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", this.token);
            if (Body != null) Request.Content = new StringContent(JsonSerializer.Serialize(Body, json), Encoding.UTF8, "application/json");
            if (this.HasSniffers) this.TransmitText(Method.Method + " " + Resource.Split('?')[0]);
            using var Response = await this.http.SendAsync(Request, HttpCompletionOption.ResponseHeadersRead, CancellationToken).ConfigureAwait(false);
            if (this.HasSniffers) this.ReceiveText("HTTP " + (int)Response.StatusCode);
            using var Stream = await Response.Content.ReadAsStreamAsync().ConfigureAwait(false);
            if (!Response.IsSuccessStatusCode)
            {
                using var Buffer = new System.IO.MemoryStream();
                await Stream.CopyToAsync(Buffer, 81920, CancellationToken).ConfigureAwait(false);
                throw new AnkiApiException(Response.StatusCode, Encoding.UTF8.GetString(Buffer.ToArray()));
            }
            if (Response.StatusCode == System.Net.HttpStatusCode.NoContent) return default!;
            return await JsonSerializer.DeserializeAsync<T>(Stream, json, CancellationToken).ConfigureAwait(false)
                ?? throw new FormatException("The Anki bridge returned an empty JSON response.");
        }

        private static string NotePath(long Id) => "v1/notes/" + PositiveId(Id);
        private static string PositiveId(long Id) => Id > 0 ? Id.ToString(CultureInfo.InvariantCulture) : throw new ArgumentOutOfRangeException(nameof(Id));
        private static string? OptionalId(long? Id) => Id.HasValue ? PositiveId(Id.Value) : null;
        private static string Query(long AfterId, int Limit, params string?[] Filters)
        {
            if (AfterId < 0) throw new ArgumentOutOfRangeException(nameof(AfterId));
            if (Limit < 1 || Limit > 1000) throw new ArgumentOutOfRangeException(nameof(Limit));
            var Result = new StringBuilder("?after_id=" + AfterId.ToString(CultureInfo.InvariantCulture) + "&limit=" + Limit.ToString(CultureInfo.InvariantCulture));
            for (int i = 0; i < Filters.Length; i += 2)
                if (Filters[i + 1] != null) Result.Append('&').Append(Filters[i]).Append('=').Append(Uri.EscapeDataString(Filters[i + 1]!));
            return Result.ToString();
        }

        public void Dispose()
        {
            if (Interlocked.Exchange(ref this.disposed, 1) == 0 && this.ownsHttp) this.http.Dispose();
        }
    }
}
