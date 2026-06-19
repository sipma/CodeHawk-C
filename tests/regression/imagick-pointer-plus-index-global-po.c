typedef unsigned short IndexPacket;
typedef long ssize_t;

static void set_index(IndexPacket *p, IndexPacket value) {
  *p = value;
}

void import_index_alpha(IndexPacket *restrict indexes, ssize_t number_pixels) {
  ssize_t x;
  ssize_t bit;

  for (x = number_pixels - 3; x > 0; x -= 4) {
    for (bit = 0; bit < 8; bit += 2) {
      set_index(indexes + x + bit / 2, 1);
    }
  }
}


int main(int argc, char **argv) {

  IndexPacket packets[10];

  import_index_alpha(packets, 10);

  return 0;
}
